"""
Web server for the Swarm orchestrator.
Provides HTTP endpoints for the web GUI and Server-Sent Events (SSE) for real-time updates.
"""

import asyncio
import json
import logging
from aiohttp import web, WSMsgType
import aiohttp_cors
from pathlib import Path
from typing import Dict, Set, Optional
import uuid

from .core import EventBus, LogEvent, EventType

logger = logging.getLogger(__name__)


class WebServer:
    """Web server for the Swarm orchestrator"""

    def __init__(self, host: str = "127.0.0.1", port: int = 8765):
        self.host = host
        self.port = port
        self.event_bus = EventBus()
        self.app = web.Application()
        self._setup_routes()
        self._setup_cors()
        # Store SSE connections
        self.sse_connections: Set[web.StreamResponse] = set()
        # Store the latest events for new SSE connections
        self.latest_events: list = []
        # Task to broadcast events
        self._broadcast_task: Optional[asyncio.Task] = None

    def _setup_routes(self):
        """Set up the web routes"""
        # Static files
        static_path = Path(__file__).parent / "static"
        self.app.router.add_static('/', static_path, name='static')

        # SSE endpoint
        self.app.router.add_get('/events', self.sse_handler)

        # Approval endpoint
        self.app.router.add_post('/api/approve', self.approve_handler)

        # Health check
        self.app.router.add_get('/health', self.health_handler)

    def _setup_cors(self):
        """Set up CORS for development"""
        cors = aiohttp_cors.setup(self.app, defaults={
            "*": aiohttp_cors.ResourceOptions(
                allow_credentials=True,
                expose_headers="*",
                allow_headers="*",
                allow_methods="*"
            )
        })
        for route in list(self.app.router.routes()):
            cors.add(route)

    async def start(self):
        """Start the web server"""
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()
        logger.info(f"Web server started at http://{self.host}:{self.port}")

        # Start event broadcaster
        self._broadcast_task = asyncio.create_task(self._broadcast_events())

        return runner

    async def stop(self):
        """Stop the web server"""
        if self._broadcast_task:
            self._broadcast_task.cancel()
            try:
                await self._broadcast_task
            except asyncio.CancelledError:
                pass

        # Close all SSE connections
        for response in list(self.sse_connections):
            try:
                await response.prepare(None)  # This will close the connection
            except Exception:
                pass
        self.sse_connections.clear()

        await self.app.shutdown()
        await self.app.cleanup()

    async def sse_handler(self, request):
        """Handle SSE connections"""
        logger.info("New SSE connection")

        # Create a streaming response
        response = web.StreamResponse(
            status=200,
            reason='OK',
            headers={
                'Content-Type': 'text/event-stream',
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Cache-Control'
            }
        )

        await response.prepare(request)

        # Send latest events to the new connection
        for event_data in self.latest_events:
            try:
                await response.write(f"data: {json.dumps(event_data)}\n\n".encode('utf-8'))
            except Exception:
                # Connection might be closed
                break

        # Add to active connections
        self.sse_connections.add(response)

        try:
            # Keep the connection alive
            while True:
                # Wait for any message from client (to detect disconnect)
                try:
                    msg = await request.receive()
                    if msg.type == aiohttp.WSMsgType.close:
                        break
                except Exception:
                    # If we can't receive, assume connection is still alive
                    await asyncio.sleep(1)
        except Exception as e:
            logger.info(f"SSE connection closed: {e}")
        finally:
            # Remove from active connections
            self.sse_connections.discard(response)
            logger.info("SSE connection removed")

        return response

    async def approve_handler(self, request):
        """Handle approval requests"""
        # Check for the custom header
        if request.headers.get('X-Swarm') != '1':
            return web.Response(
                text='Forbidden: Missing or invalid X-Swarm header',
                status=403
            )

        try:
            data = await request.json()
            task_id = data.get('task_id')
            action = data.get('action')  # 'approve' or 'abort'

            if not task_id or action not in ['approve', 'abort']:
                return web.Response(
                    text='Bad request: task_id and action (approve/abort) required',
                    status=400
                )

            # Publish approval event to the event bus
            event_type = EventType.APPROVED if action == 'approve' else EventType.APPROVAL
            await self.event_bus.publish(LogEvent(
                task_id=task_id,
                level="info",
                message=f"Task {task_id} {action}d via web GUI"
            ).to_event())

            logger.info(f"Received {action} for task {task_id}")

            return web.json_response({'status': 'success', 'task_id': task_id, 'action': action})

        except json.JSONDecodeError:
            return web.Response(
                text='Bad request: Invalid JSON',
                status=400
            )
        except Exception as e:
            logger.exception("Error handling approval request")
            return web.Response(
                text=f'Internal server error: {str(e)}',
                status=500
            )

    async def health_handler(self, request):
        """Health check endpoint"""
        return web.json_response({
            'status': 'healthy',
            'service': 'swarm-orchestrator',
            'version': '0.1.0'
        })

    async def _broadcast_events(self):
        """Broadcast events to all SSE connections"""
        # Subscribe to the event bus
        queue = asyncio.Queue()
        self.event_bus._subscribers.add(queue)

        try:
            while True:
                # Wait for an event
                event = await queue.get()

                # Convert event to dict for JSON serialization
                event_data = {
                    'type': event.type.value,
                    'timestamp': event.timestamp.isoformat(),
                    'data': event.data
                }

                # Store latest events (keep last 100)
                self.latest_events.append(event_data)
                if len(self.latest_events) > 100:
                    self.latest_events.pop(0)

                # Broadcast to all SSE connections
                if self.sse_connections:
                    message = f"data: {json.dumps(event_data)}\n\n"
                    encoded = message.encode('utf-8')
                    # Use a list to avoid issues with set changing during iteration
                    connections = list(self.sse_connections)
                    for response in connections:
                        try:
                            await response.write(encoded)
                        except Exception:
                            # Remove broken connections
                            self.sse_connections.discard(response)
        except asyncio.CancelledError:
            pass
        finally:
            # Clean up subscription
            self.event_bus._subscribers.discard(queue)


# Convenience function to run the server
async def run_web_server(host: str = "127.0.0.1", port: int = 8765):
    """Run the web server"""
    server = WebServer(host, port)
    runner = await server.start()

    try:
        # Keep the server running
        while True:
            await asyncio.sleep(3600)  # Sleep for an hour
    except KeyboardInterrupt:
        logger.info("Shutting down web server...")
    finally:
        await server.stop()
        await runner.cleanup()


if __name__ == "__main__":
    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Run the server
    asyncio.run(run_web_server())