#!/usr/bin/env python3
"""
Tests for the web server module.
"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
import sys
import os

# Add the swarm directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent / "swarm"))

try:
    from aiohttp import test_utils, web
    from web_server import WebServer
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False
    WebServer = None  # type: ignore


@unittest.skipIf(not HAS_AIOHTTP, "aiohttp not installed")
class TestWebServer(unittest.TestCase):
    """Test the web server functionality"""

    def setUp(self):
        self.server = WebServer(host="127.0.0.1", port=8766)  # Use different port for testing

    def test_web_server_creation(self):
        """Test that the web server can be created"""
        self.assertEqual(self.server.host, "127.0.0.1")
        self.assertEqual(self.server.port, 8766)
        self.assertIsInstance(self.server.event_bus, type(self.server.event_bus))
        self.assertIsInstance(self.server.app, web.Application)

    def test_route_setup(self):
        """Test that routes are set up correctly"""
        # Get all routes
        routes = list(self.server.app.router.routes())
        route_paths = [route.resource.canonical for route in routes]

        # Check that we have the expected routes
        self.assertIn('/', route_paths)  # Static files
        self.assertIn('/events', route_paths)  # SSE endpoint
        self.assertIn('/api/approve', route_paths)  # Approval endpoint
        self.assertIn('/health', route_paths)  # Health check

    def test_health_endpoint(self):
        """Test the health check endpoint"""
        # Create a test client
        async def test_health():
            client = test_utils.TestClient(self.server.app)
            await client.start_server()
            try:
                resp = await client.request('GET', '/health')
                self.assertEqual(resp.status, 200)

                data = await resp.json()
                self.assertEqual(data['status'], 'healthy')
                self.assertEqual(data['service'], 'swarm-orchestrator')
            finally:
                await client.close()

        asyncio.run(test_health())

    def test_sse_endpoint(self):
        """Test the SSE endpoint"""
        async def test_sse():
            client = test_utils.TestClient(self.server.app)
            await client.start_server()
            try:
                resp = await client.request('GET', '/events')
                self.assertEqual(resp.status, 200)
                self.assertEqual(resp.headers.get('Content-Type'), 'text/event-stream')
                await client.close()
            except Exception as e:
                await client.close()
                raise e

        asyncio.run(test_sse())

    def test_approve_endpoint_missing_header(self):
        """Test approval endpoint without the required header"""
        async def test_approve_no_header():
            client = test_utils.TestClient(self.server.app)
            await client.start_server()
            try:
                resp = await client.request(
                    'POST',
                    '/api/approve',
                    json={'task_id': 'test', 'action': 'approve'}
                )
                self.assertEqual(resp.status, 403)
                self.assertIn('Forbidden', await resp.text())
            finally:
                await client.close()

        asyncio.run(test_approve_no_header())

    def test_approve_endpoint_bad_request(self):
        """Test approval endpoint with bad request data"""
        async def test_approve_bad_request():
            client = test_utils.TestClient(self.server.app)
            await client.start_server()
            try:
                # Missing task_id
                resp = await client.request(
                    'POST',
                    '/api/approve',
                    headers={'X-Swarm': '1'},
                    json={'action': 'approve'}
                )
                self.assertEqual(resp.status, 400)

                # Invalid action
                resp = await client.request(
                    'POST',
                    '/api/approve',
                    headers={'X-Swarm': '1'},
                    json={'task_id': 'test', 'action': 'invalid'}
                )
                self.assertEqual(resp.status, 400)
            finally:
                await client.close()

        asyncio.run(test_approve_bad_request())

    def test_approve_endpoint_valid_request(self):
        """Test approval endpoint with valid request"""
        async def test_approve_valid():
            client = test_utils.TestClient(self.server.app)
            await client.start_server()
            try:
                resp = await client.request(
                    'POST',
                    '/api/approve',
                    headers={'X-Swarm': '1'},
                    json={'task_id': 'test_task', 'action': 'approve'}
                )
                self.assertEqual(resp.status, 200)

                data = await resp.json()
                self.assertEqual(data['status'], 'success')
                self.assertEqual(data['task_id'], 'test_task')
                self.assertEqual(data['action'], 'approve')
            finally:
                await client.close()

        asyncio.run(test_approve_valid())


if __name__ == '__main__':
    unittest.main()