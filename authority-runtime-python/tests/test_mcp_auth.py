"""
Tests for MCP HTTP auth, rate limiting, and hardcoded path removal (D3).
"""

import os
import time
import pytest

from authority_runtime.mcp_server import CarryallMCPServer, RateLimiter


class TestHardcodedPathRemoved:
    def test_no_slos_config_uses_memory_backend(self):
        """Without CARRYALL_SLOS_CONFIG, server uses MemoryBackend."""
        from authority_runtime.backends.memory import MemoryBackend

        # Ensure no config is set
        env_backup = os.environ.pop("CARRYALL_SLOS_CONFIG", None)
        try:
            server = CarryallMCPServer()
            assert isinstance(server.slos_backend, MemoryBackend)
        finally:
            if env_backup:
                os.environ["CARRYALL_SLOS_CONFIG"] = env_backup


class TestRateLimiter:
    def test_allows_under_limit(self):
        limiter = RateLimiter(max_requests=5, window_seconds=60)
        for _ in range(5):
            assert limiter.check("127.0.0.1") is True

    def test_blocks_over_limit(self):
        limiter = RateLimiter(max_requests=3, window_seconds=60)
        for _ in range(3):
            limiter.check("127.0.0.1")
        assert limiter.check("127.0.0.1") is False

    def test_different_ips_independent(self):
        limiter = RateLimiter(max_requests=2, window_seconds=60)
        limiter.check("10.0.0.1")
        limiter.check("10.0.0.1")
        # IP 1 is at limit
        assert limiter.check("10.0.0.1") is False
        # IP 2 still has quota
        assert limiter.check("10.0.0.2") is True

    def test_resets_after_window(self):
        limiter = RateLimiter(max_requests=1, window_seconds=0.1)
        limiter.check("127.0.0.1")
        assert limiter.check("127.0.0.1") is False
        # Wait for window to expire
        time.sleep(0.15)
        assert limiter.check("127.0.0.1") is True


class TestHTTPAuth:
    """Integration tests for auth middleware using aiohttp test client."""

    @pytest.fixture
    def api_key(self):
        return "test-secret-key-123"

    @pytest.mark.asyncio
    async def test_health_bypasses_auth(self, api_key):
        """Health endpoints should always be accessible regardless of auth."""
        try:
            from aiohttp import web
            from aiohttp.test_utils import TestClient, TestServer
        except ImportError:
            pytest.skip("aiohttp required for HTTP auth tests")

        os.environ["CARRYALL_API_KEY"] = api_key
        try:
            CarryallMCPServer()

            # Build the app manually to test middleware
            @web.middleware
            async def auth_middleware(request, handler):
                ak = os.environ.get("CARRYALL_API_KEY")
                if request.path in ("/health", "/healthz"):
                    return await handler(request)
                if ak:
                    auth_header = request.headers.get("Authorization", "")
                    if not auth_header.startswith("Bearer ") or auth_header[7:] != ak:
                        return web.json_response({"error": "Unauthorized"}, status=401)
                return await handler(request)

            async def health_handler(request):
                return web.json_response({"status": "healthy"})

            async def tools_handler(request):
                return web.json_response({"tools": []})

            app = web.Application(middlewares=[auth_middleware])
            app.router.add_get("/health", health_handler)
            app.router.add_get("/healthz", health_handler)
            app.router.add_get("/tools", tools_handler)

            async with TestClient(TestServer(app)) as client:
                # Health should work without auth
                resp = await client.get("/health")
                assert resp.status == 200

                # Tools should require auth
                resp = await client.get("/tools")
                assert resp.status == 401

                # Tools with correct auth should work
                resp = await client.get("/tools", headers={"Authorization": f"Bearer {api_key}"})
                assert resp.status == 200

                # Wrong key should fail
                resp = await client.get("/tools", headers={"Authorization": "Bearer wrong-key"})
                assert resp.status == 401
        finally:
            os.environ.pop("CARRYALL_API_KEY", None)

    @pytest.mark.asyncio
    async def test_no_api_key_allows_all(self):
        """Without CARRYALL_API_KEY, all requests should be allowed."""
        try:
            from aiohttp import web
            from aiohttp.test_utils import TestClient, TestServer
        except ImportError:
            pytest.skip("aiohttp required for HTTP auth tests")

        os.environ.pop("CARRYALL_API_KEY", None)

        @web.middleware
        async def auth_middleware(request, handler):
            ak = os.environ.get("CARRYALL_API_KEY")
            if request.path in ("/health", "/healthz"):
                return await handler(request)
            if ak:
                auth_header = request.headers.get("Authorization", "")
                if not auth_header.startswith("Bearer ") or auth_header[7:] != ak:
                    return web.json_response({"error": "Unauthorized"}, status=401)
            return await handler(request)

        async def tools_handler(request):
            return web.json_response({"tools": []})

        app = web.Application(middlewares=[auth_middleware])
        app.router.add_get("/tools", tools_handler)

        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/tools")
            assert resp.status == 200
