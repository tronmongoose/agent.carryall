"""
Tests for structured logging configuration (D3).
"""

import json
import logging
import os



class TestJSONFormatter:
    def test_json_formatter_output(self):
        from authority_runtime.logging_config import JSONFormatter

        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="authority_runtime.test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["level"] == "INFO"
        assert parsed["message"] == "Test message"
        assert parsed["logger"] == "authority_runtime.test"
        assert "timestamp" in parsed

    def test_request_id_propagation(self):
        from authority_runtime.logging_config import JSONFormatter, new_request_id, request_id_var

        rid = new_request_id()
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="test.py", lineno=1,
            msg="test", args=(), exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["request_id"] == rid
        # Cleanup
        request_id_var.set("")

    def test_extra_fields_included(self):
        from authority_runtime.logging_config import JSONFormatter

        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="test.py", lineno=1,
            msg="test", args=(), exc_info=None,
        )
        record.agent_id = "test-agent"
        record.duration_ms = 42.5
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["agent_id"] == "test-agent"
        assert parsed["duration_ms"] == 42.5

    def test_configure_logging_json_format(self):
        from authority_runtime.logging_config import configure_logging

        os.environ.pop("CARRYALL_LOG_FORMAT", None)
        os.environ.pop("CARRYALL_LOG_LEVEL", None)
        configure_logging()

        root = logging.getLogger("authority_runtime")
        assert root.level == logging.INFO
        assert len(root.handlers) == 1
        from authority_runtime.logging_config import JSONFormatter
        assert isinstance(root.handlers[0].formatter, JSONFormatter)

    def test_configure_logging_text_format(self):
        from authority_runtime.logging_config import configure_logging

        os.environ["CARRYALL_LOG_FORMAT"] = "text"
        try:
            configure_logging()
            root = logging.getLogger("authority_runtime")
            from authority_runtime.logging_config import JSONFormatter
            assert not isinstance(root.handlers[0].formatter, JSONFormatter)
        finally:
            os.environ.pop("CARRYALL_LOG_FORMAT", None)
            configure_logging()  # Reset

    def test_log_level_from_env(self):
        from authority_runtime.logging_config import configure_logging

        os.environ["CARRYALL_LOG_LEVEL"] = "DEBUG"
        try:
            configure_logging()
            root = logging.getLogger("authority_runtime")
            assert root.level == logging.DEBUG
        finally:
            os.environ.pop("CARRYALL_LOG_LEVEL", None)
            configure_logging()  # Reset
