"""
Git utilities for the Swarm orchestrator.
Handles git operations, worktree management, and merge conflict resolution.
"""

import asyncio
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any
import logging
import os

logger = logging.getLogger(__name__)


class GitError(Exception):
    """Custom exception for git operations"""
    pass


@dataclass
class GitBranchInfo:
    """Information about a git branch"""
    name: str
    commit: str
    is_current: bool
    is_remote: bool = False


@dataclass
class WorktreeInfo:
    """Information about a git worktree"""
    path: Path
    branch: str
    commit: str
    is_current: bool = False
    is_bare: bool = False


@dataclass
class MergeResult:
    """Result of a merge operation"""
    success: bool
    conflict: bool = False
    message: str = ""
    commit_hash: Optional[str] = None


class GitManager:
    """Manages git repository operations"""

    def __init__(self, repo_path: Path):
        self.repo_path = repo_path.resolve()
        self._ensure_git_repo()

    def _ensure_git_repo(self):
        """Ensure the path is a valid git repository"""
        if not (self.repo_path / ".git").exists():
            raise GitError(f"Not a git repository: {self.repo_path}")

        # Ensure .orch/ is excluded from git
        info_exclude = self.repo_path / ".git" / "info" / "exclude"
        info_exclude.parent.mkdir(parents=True, exist_ok=True)

        exclude_content = ""
        if info_exclude.exists():
            exclude_content = info_exclude.read_text()

        if ".orch/" not in exclude_content:
            with open(info_exclude, "a") as f:
                f.write("\n.orch/\n")

    async def _run_git_command(self, *args, check: bool = True) -> subprocess.CompletedProcess:
        """Run a git command asynchronously"""
        cmd = ["git"] + list(args)
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=self.repo_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()

            if check and process.returncode != 0:
                raise GitError(
                    f"Git command failed: {' '.join(cmd)}\n"
                    f"stdout: {stdout.decode()}\n"
                    f"stderr: {stderr.decode()}"
                )

            return subprocess.CompletedProcess(
                args=cmd,
                returncode=process.returncode,
                stdout=stdout.decode(),
                stderr=stderr.decode()
            )
        except Exception as e:
            if isinstance(e, GitError):
                raise
            raise GitError(f"Failed to run git command: {e}")

    async def get_current_branch(self) -> str:
        """Get the current branch name"""
        result = await self._run_git_command("rev-parse", "--abbrev-ref", "HEAD")
        return result.stdout.strip()

    async def get_current_commit(self) -> str:
        """Get the current commit hash"""
        result = await self._run_git_command("rev-parse", "HEAD")
        return result.stdout.strip()

    async def get_branch_commit(self, branch: str) -> str:
        """Get the commit hash for a branch"""
        result = await self._run_git_command("rev-parse", branch)
        return result.stdout.strip()

    async def branch_exists(self, branch: str) -> bool:
        """Check if a branch exists"""
        try:
            await self._run_git_command("rev-parse", "--verify", branch, check=False)
            return True
        except GitError:
            return False

    async def create_branch(self, branch: str, start_point: Optional[str] = None):
        """Create a new branch"""
        if start_point:
            await self._run_git_command("branch", branch, start_point)
        else:
            await self._run_git_command("branch", branch)
        logger.debug(f"Created branch: {branch}")

    async def delete_branch(self, branch: str, force: bool = False):
        """Delete a branch"""
        flag = "-D" if force else "-d"
        await self._run_git_command("branch", flag, branch)
        logger.debug(f"Deleted branch: {branch}")

    async def checkout_branch(self, branch: str, create_new: bool = False):
        """Checkout a branch"""
        if create_new:
            await self._run_git_command("checkout", "-b", branch)
        else:
            await self._run_git_command("checkout", branch)
        logger.debug(f"Checked out branch: {branch}")

    async def list_branches(self) -> List[GitBranchInfo]:
        """List all branches"""
        result = await self._run_git_command("branch", "-a", "--format=%(refname:short)%00%(objectname:short)%00%(HEAD)")
        branches = []

        for line in result.stdout.strip().split('\n'):
            if not line:
                continue

            parts = line.split('\x00')
            if len(parts) >= 3:
                name, commit, head_flag = parts[0], parts[1], parts[2]
                is_current = head_flag == "*"
                is_remote = name.startswith("remotes/")

                # Clean up remote branch names for display
                display_name = name
                if is_remote:
                    display_name = name.replace("remotes/", "")

                branches.append(GitBranchInfo(
                    name=display_name,
                    commit=commit,
                    is_current=is_current,
                    is_remote=is_remote
                ))

        return branches

    async def get_worktrees(self) -> List[WorktreeInfo]:
        """List all worktrees"""
        result = await self._run_git_command("worktree", "list", "--porcelain")

        worktrees = []
        current_worktree = {}

        for line in result.stdout.split('\n'):
            if line.startswith("worktree "):
                if current_worktree:
                    worktrees.append(WorktreeInfo(**current_worktree))
                current_worktree = {"path": Path(line.split(" ", 1)[1])}
            elif line.startswith("branch "):
                # refs/heads/branch_name -> branch_name
                branch_ref = line.split(" ", 1)[1]
                if branch_ref.startswith("refs/heads/"):
                    current_worktree["branch"] = branch_ref[11:]
                else:
                    current_worktree["branch"] = branch_ref
            elif line.startswith("HEAD "):
                current_worktree["commit"] = line.split(" ", 1)[1]
            elif line == "":
                if current_worktree:
                    worktrees.append(WorktreeInfo(**current_worktree))
                    current_worktree = {}

        # Handle last worktree if file doesn't end with blank line
        if current_worktree:
            worktrees.append(WorktreeInfo(**current_worktree))

        # Set is_current flag
        for wt in worktrees:
            wt.is_current = wt.path == self.repo_path

        return worktrees

    async def create_worktree(self, branch: str, worktree_path: Path):
        """Create a new worktree"""
        worktree_path = worktree_path.resolve()

        # Check if branch exists, if not create it
        if not await self.branch_exists(branch):
            await self.create_branch(branch)

        await self._run_git_command("worktree", "add", str(worktree_path), branch)
        logger.info(f"Created worktree at {worktree_path} for branch {branch}")

    async def remove_worktree(self, worktree_path: Path, force: bool = False):
        """Remove a worktree"""
        worktree_path = worktree_path.resolve()

        # Check if worktree exists
        worktrees = await self.get_worktrees()
        paths = [wt.path for wt in worktrees]

        if worktree_path not in paths:
            logger.warning(f"Worktree not found: {worktree_path}")
            return

        flag = "--force" if force else ""
        await self._run_git_command("worktree", "remove", flag, str(worktree_path))
        logger.info(f"Removed worktree: {worktree_path}")

    async def has_unmerged_files(self) -> bool:
        """Check if there are unmerged files (merge conflicts)"""
        result = await self._run_git_command("diff", "--name-only", "--diff-filter=U", check=False)
        unmerged_files = result.stdout.strip()
        return bool(unmerged_files)

    async def merge_branch(self, source_branch: str, target_branch: str = None,
                          no_ff: bool = False, squash: bool = False) -> MergeResult:
        """
        Merge source_branch into target_branch (or current branch if target_branch not specified)
        """
        if target_branch is None:
            target_branch = await self.get_current_branch()

        # Checkout target branch
        await self.checkout_branch(target_branch)

        # Prepare merge command
        cmd = ["merge"]
        if no_ff:
            cmd.append("--no-ff")
        if squash:
            cmd.append("--squash")
        cmd.append(source_branch)

        try:
            result = await self._run_git_command(*cmd, check=False)

            # Check if merge had conflicts
            conflict = await self.has_unmerged_files()

            if result.returncode == 0 and not conflict:
                # Successful merge
                commit_hash = await self.get_current_commit()
                return MergeResult(
                    success=True,
                    conflict=False,
                    message="Merge successful",
                    commit_hash=commit_hash
                )
            elif conflict:
                # Merge conflicts
                return MergeResult(
                    success=False,
                    conflict=True,
                    message="Merge conflicts detected",
                    commit_hash=None
                )
            else:
                # Other merge failure
                return MergeResult(
                    success=False,
                    conflict=False,
                    message=f"Merge failed: {result.stderr}",
                    commit_hash=None
                )
        except Exception as e:
            return MergeResult(
                success=False,
                conflict=False,
                message=f"Merge exception: {str(e)}",
                commit_hash=None
            )

    async def abort_merge(self):
        """Abort the current merge operation"""
        await self._run_git_command("merge", "--abort")
        logger.info("Aborted merge operation")

    async def commit_all(self, message: str) -> str:
        """Commit all changes and return the commit hash"""
        await self._run_git_command("add", "-A")
        await self._run_git_command("commit", "-m", message)
        commit_hash = await self.get_current_commit()
        logger.info(f"Committed changes: {commit_hash}")
        return commit_hash

    async def reset_hard(self, commit: str):
        """Hard reset to a commit"""
        await self._run_git_command("reset", "--hard", commit)
        logger.info(f"Hard reset to {commit}")

    async def push_branch(self, branch: str, remote: str = "origin"):
        """Push a branch to remote"""
        await self._run_git_command("push", remote, branch)
        logger.info(f"Pushed branch {branch} to {remote}")

    async def fetch_remote(self, remote: str = "origin"):
        """Fetch from remote"""
        await self._run_git_command("fetch", remote)
        logger.info(f"Fetched from {remote}")


class WorktreeManager:
    """High-level worktree management for the orchestrator"""

    def __init__(self, repo_path: Path):
        self.git_manager = GitManager(repo_path)
        self.repo_path = repo_path
        self._worktree_lock = asyncio.Lock()

    async def create_worktree(self, branch_name: str, worktree_path: Path) -> Path:
        """Create a worktree with automatic branch creation"""
        async with self._worktree_lock:
            # Generate unique branch name if not provided
            if not branch_name:
                branch_name = f"worktree-{uuid.uuid4().hex[:8]}"

            # Ensure branch exists
            if not await self.git_manager.branch_exists(branch_name):
                await self.git_manager.create_branch(branch_name)

            # Create worktree
            await self.git_manager.create_worktree(branch_name, worktree_path)

            logger.info(f"Created worktree: {worktree_path} (branch: {branch_name})")
            return worktree_path

    async def remove_worktree(self, worktree_path: Path, force: bool = False):
        """Remove a worktree"""
        async with self._worktree_lock:
            await self.git_manager.remove_worktree(worktree_path, force)
            logger.info(f"Removed worktree: {worktree_path}")

    async def list_worktrees(self) -> List[WorktreeInfo]:
        """List all worktrees"""
        return await self.git_manager.get_worktrees()

    async def prepare_integration_branch(self, run_id: str) -> str:
        """
        Prepare or get the integration branch for a run.
        Returns the branch name.
        """
        branch_name = f"orch/{run_id}/integration"

        # Check if integration branch exists
        if not await self.git_manager.branch_exists(branch_name):
            # Create it from current HEAD
            current_commit = await self.git_manager.get_current_commit()
            await self.git_manager.create_branch(branch_name, current_commit)
            logger.info(f"Created integration branch: {branch_name}")
        else:
            logger.debug(f"Using existing integration branch: {branch_name}")

        return branch_name

    async def prepare_task_worktree(self,
                                  task_id: str,
                                  run_id: str,
                                  base_branch: Optional[str] = None) -> Tuple[Path, str]:
        """
        Prepare a worktree for a specific task.
        Returns (worktree_path, branch_name).
        """
        async with self._worktree_lock:
            # Generate unique identifiers
            task_branch = f"orch/{run_id}/task-{task_id}-{uuid.uuid4().hex[:6]}"
            worktree_path = self.repo_path / ".orch" / f"run-{run_id}" / f"task-{task_id}"

            # Determine base branch
            if base_branch is None:
                base_branch = await self.prepare_integration_branch(run_id)

            # Create task branch from base
            await self.git_manager.create_branch(task_branch, base_branch)

            # Create worktree
            await self.git_manager.create_worktree(task_branch, worktree_path)

            logger.info(f"Prepared task worktree: {worktree_path} (branch: {task_branch})")
            return worktree_path, task_branch

    async def cleanup_task_resources(self, run_id: str, task_id: str,
                                   branch_name: str, worktree_path: Path):
        """Clean up resources after a task is completed"""
        async with self._worktree_lock:
            # Remove worktree
            await self.remove_worktree(worktree_path, force=True)

            # Optionally delete the branch (commented out for debugging)
            # await self.git_manager.delete_branch(branch_name, force=True)

            logger.debug(f"Cleaned up task resources: {worktree_path}, {branch_name}")


# Convenience functions
async def init_git_repo(repo_path: Path) -> GitManager:
    """Initialize a git repository at the given path"""
    repo_path = repo_path.resolve()
    repo_path.mkdir(parents=True, exist_ok=True)

    # Initialize git repo
    process = await asyncio.create_subprocess_exec(
        "git", "init",
        cwd=repo_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        raise GitError(f"Failed to init git repo: {stderr.decode()}")

    # Configure basic settings
    await asyncio.create_subprocess_exec(
        "git", "config", "user.name", "Swarm Orchestrator",
        cwd=repo_path
    ).wait()

    await asyncio.create_subprocess_exec(
        "git", "config", "user.email", "orchestrator@swarm.local",
        cwd=repo_path
    ).wait()

    return GitManager(repo_path)


if __name__ == "__main__":
    # Simple test
    import asyncio
    import tempfile

    async def test():
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir) / "test_repo"
            git_manager = await init_git_repo(repo_path)

            # Create initial commit
            (repo_path / "README.md").write_text("# Test Repo\n")
            await git_manager._run_git_command("add", "README.md")
            await git_manager._run_git_command("commit", "-m", "Initial commit")

            # Test branch operations
            await git_manager.create_branch("feature-test")
            assert await git_manager.branch_exists("feature-test")

            # Test worktree operations
            worktree_manager = WorktreeManager(repo_path)
            worktree_path = await worktree_manager.create_worktree(
                "feature-test",
                repo_path / "test_worktree"
            )

            assert worktree_path.exists()
            worktrees = await worktree_manager.list_worktrees()
            assert len([wt for wt in worktrees if wt.path == worktree_path]) == 1

            await worktree_manager.remove_worktree(worktree_path)
            assert not worktree_path.exists()

            print("GitManager and WorktreeManager tests passed!")

    asyncio.run(test())