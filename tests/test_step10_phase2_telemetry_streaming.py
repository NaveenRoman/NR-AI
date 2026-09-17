"""
Unit and Integration Tests for Step 10 Phase 2:
Remote Telemetry & Screen / Frame Streaming Protocol.

Validates all 40 required security contracts and streaming invariants:
1. Telemetry event generation for each event type (12 types)
2. Telemetry validation and sequence number ordering
3. Telemetry max event size rejection (> 32 KB)
4. Telemetry max payload size rejection (> 16 KB)
5. Telemetry requires authentication and valid session
6. Telemetry requires READ_TELEMETRY scope
7. Unauthenticated telemetry request rejection
8. Frame creation and checksum computation (SHA-256)
9. Frame dimension boundary enforcement (max 1920x1080)
10. Frame byte size limit enforcement (> 512 KB rejected)
11. Frame encoding whitelist validation (JPEG, PNG, WEBP, MOCK_RGB)
12. Checksum tampering detection on frame payload
13. Frame metadata extraction (never leaking raw pixels to metadata dict)
14. Framerate clamping (requested 60 FPS clamped to 30, <= 0 clamped to 1)
15. Backpressure queue enqueue and drop-oldest policy on overflow
16. Backpressure queue byte limit enforcement (cumulative frame size)
17. Stream state machine: valid transitions (IDLE -> STARTING -> ACTIVE -> PAUSED -> ACTIVE -> STOPPING -> STOPPED)
18. Stream state machine: invalid transition rejection
19. Stream session creation and device ownership enforcement
20. Stream session cross-device access rejection
21. Stream inactivity timeout (auto-transition to STOPPED)
22. Stream lifetime expiration (exceeding 2h max lifetime stops stream)
23. Stream connection loss & reconnect within grace window
24. Stream reconnect attempt limit exceeded -> stream terminated
25. Emergency stop immediate halt: active streams transition to STOPPED immediately
26. Emergency stop: frame acquisition blocked while emergency stop is active
27. Emergency stop: zero LLM dependency (pure deterministic signal)
28. Audit logging: stream start/stop/pause/resume logged with metadata
29. Audit logging: telemetry reads logged without leaking sensitive payloads
30. Audit logging: pixel data strictly excluded from all audit records
31. Model isolation: LLM blocked from calling screen capture or stream methods directly
32. Model isolation: LLM cannot access raw streaming sockets or queues
33. Observation-only boundary: remote control actions rejected with 403 Forbidden
34. Zero shell=True subprocesses across all Phase 2 streaming modules
35. Localhost binding only: streaming server cannot bind to 0.0.0.0
36. Mock screen capture engine generates valid synthetic frames with DEVELOPMENT_MOCK_CAPTURE label
37. Local screen capture engine handles headless environment gracefully
38. Maximum concurrent streams per device limit enforcement
39. Maximum global concurrent streams limit enforcement
40. Backward compatibility with Step 10 Phase 1 endpoints
"""

import ast
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.remote import (
    ALLOWED_FRAME_ENCODINGS,
    DEFAULT_FPS,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DeviceIdentity,
    EmergencyStopController,
    EmergencyStopStatus,
    EncryptedPacket,
    FrameEncoding,
    LocalScreenCaptureEngine,
    MAX_CONCURRENT_STREAMS_PER_DEVICE,
    MAX_FRAME_BYTES,
    MAX_FRAME_HEIGHT,
    MAX_FRAME_WIDTH,
    MAX_FRAMES_PER_SECOND,
    MAX_GLOBAL_CONCURRENT_STREAMS,
    MAX_PAYLOAD_BYTES,
    MAX_QUEUE_BYTES,
    MAX_QUEUED_FRAMES,
    MAX_RECONNECT_ATTEMPTS,
    MAX_RECONNECT_WINDOW_SECONDS,
    MAX_REQUEST_BYTES,
    MAX_STREAM_LIFETIME_SECONDS,
    MAX_TELEMETRY_EVENT_BYTES,
    MAX_TELEMETRY_PAYLOAD_BYTES,
    MIN_FRAMES_PER_SECOND,
    MockScreenCaptureEngine,
    ModelIsolationGate,
    PairingManager,
    PCIdentity,
    PhonePermissionScope,
    PROHIBITED_ACTIONS,
    RateLimiter,
    ScreenCaptureEngine,
    SecureDashboardServer,
    SecureGateway,
    SecureRequest,
    SecureResponse,
    SecureTransport,
    SecurityAuditLogger,
    SecurityBindingError,
    Session,
    SessionManager,
    StreamBackpressureQueue,
    StreamFrame,
    StreamManager,
    StreamSession,
    StreamState,
    StreamStateError,
    STREAM_INACTIVITY_TIMEOUT_SECONDS,
    TelemetryDispatcher,
    TelemetryEvent,
    TelemetryEventType,
    VALID_STREAM_TRANSITIONS,
    authorize_action,
    create_stream_frame,
    parse_and_validate_request,
    redact_sensitive_data,
    validate_frame,
)
from app.remote.telemetry import validate_telemetry_event


class TestTelemetryProtocol(unittest.TestCase):
    """Tests 1 through 7: Telemetry Protocol, Dispatcher, Scopes and Validation."""

    def setUp(self):
        self.dispatcher = TelemetryDispatcher()
        self.session_id = "test-session-001"
        self.device_id = "test-device-001"

    def test_01_telemetry_event_generation_all_types(self):
        """1. Telemetry event generation for all 12 TelemetryEventType values."""
        all_types = list(TelemetryEventType)
        self.assertEqual(len(all_types), 12)

        for event_type in all_types:
            event = self.dispatcher.emit_event(
                device_id=self.device_id,
                session_id=self.session_id,
                event_type=event_type,
                payload={"sample_key": f"value_for_{event_type.value}"},
            )
            self.assertIsNotNone(event.event_id)
            self.assertEqual(event.device_id, self.device_id)
            self.assertEqual(event.session_id, self.session_id)
            self.assertEqual(event.event_type, event_type.value)
            self.assertGreater(event.sequence_number, 0)
            self.assertGreater(event.timestamp, 0.0)
            self.assertEqual(event.payload.get("sample_key"), f"value_for_{event_type.value}")

    def test_02_telemetry_validation_and_sequence_ordering(self):
        """2. Telemetry validation and sequence number ordering."""
        # Emit 3 events
        e1 = self.dispatcher.emit_event(self.device_id, self.session_id, TelemetryEventType.STATUS_UPDATE, {"step": 1})
        e2 = self.dispatcher.emit_event(self.device_id, self.session_id, TelemetryEventType.TASK_PROGRESS, {"step": 2})
        e3 = self.dispatcher.emit_event(self.device_id, self.session_id, TelemetryEventType.AGENT_STATE, {"step": 3})

        self.assertEqual(e1.sequence_number, 1)
        self.assertEqual(e2.sequence_number, 2)
        self.assertEqual(e3.sequence_number, 3)

        # Retrieve all events
        all_events = self.dispatcher.get_events(self.session_id, since_sequence=0)
        self.assertEqual(len(all_events), 3)

        # Retrieve events since sequence 1
        filtered_events = self.dispatcher.get_events(self.session_id, since_sequence=1)
        self.assertEqual(len(filtered_events), 2)
        self.assertEqual(filtered_events[0].sequence_number, 2)
        self.assertEqual(filtered_events[1].sequence_number, 3)

        # Validation function checks
        valid, msg, validated_event = validate_telemetry_event(e1.to_dict())
        self.assertTrue(valid)
        self.assertIsNotNone(validated_event)

        # Invalid type check
        bad_data = e1.to_dict()
        bad_data["event_type"] = "NON_EXISTENT_TYPE"
        valid, msg, _ = validate_telemetry_event(bad_data)
        self.assertFalse(valid)
        self.assertIn("UNKNOWN_EVENT_TYPE", msg)

    def test_03_telemetry_max_event_size_rejection(self):
        """3. Telemetry max event size rejection (> 32 KB)."""
        huge_payload = {"padding": "X" * (MAX_TELEMETRY_EVENT_BYTES + 100)}
        huge_event_dict = {
            "event_id": "evt-oversized",
            "device_id": self.device_id,
            "session_id": self.session_id,
            "event_type": TelemetryEventType.STATUS_UPDATE.value,
            "timestamp": time.time(),
            "sequence_number": 1,
            "payload": huge_payload,
        }
        valid, msg, _ = validate_telemetry_event(huge_event_dict)
        self.assertFalse(valid)
        self.assertIn("exceeds limit", msg)

    def test_04_telemetry_max_payload_size_rejection(self):
        """4. Telemetry max payload size rejection (> 16 KB)."""
        oversized_payload = {"padding": "Y" * (MAX_TELEMETRY_PAYLOAD_BYTES + 50)}
        event_dict = {
            "event_id": "evt-payload-oversized",
            "device_id": self.device_id,
            "session_id": self.session_id,
            "event_type": TelemetryEventType.STATUS_UPDATE.value,
            "timestamp": time.time(),
            "sequence_number": 1,
            "payload": oversized_payload,
        }
        valid, msg, _ = validate_telemetry_event(event_dict)
        self.assertFalse(valid)
        self.assertIn("exceeds limit", msg)

    def test_05_telemetry_authentication_and_session_required(self):
        """5. Telemetry requires authentication and valid session."""
        gateway = SecureGateway()
        # Request with bogus session id
        req = SecureRequest(
            request_id="req-telemetry-01",
            device_id="unregistered-dev",
            session_id="bogus-session-token",
            action="telemetry.read",
            payload={"since_sequence": 0},
            scope=PhonePermissionScope.READ_TELEMETRY.value,
            timestamp=time.time(),
            nonce="nonce-telemetry-01",
            signature="bogus-signature",
        )
        res = gateway.handle_secure_command(req.to_json(), "127.0.0.1")
        self.assertEqual(res.code, 401)
        self.assertEqual(res.status, "DENIED")

    def test_06_telemetry_read_scope_enforcement(self):
        """6. Telemetry requires READ_TELEMETRY scope."""
        pairing_mgr = PairingManager()
        code = pairing_mgr.generate_pairing_code()
        pairing_mgr.confirm_pairing("dev-scope-test", code)
        session_mgr = SessionManager(pairing_mgr)
        # Create session with ONLY READ_STATUS scope (no READ_TELEMETRY)
        _, _, session = session_mgr.create_session(
            "dev-scope-test",
            scopes=[PhonePermissionScope.READ_STATUS],
        )

        auth_res = authorize_action(
            action="telemetry.read",
            granted_scopes=session.scopes,
            is_emergency_active=False,
        )
        self.assertFalse(auth_res.allowed)
        self.assertEqual(auth_res.decision_code, "SCOPE_DENIED")

        # Now test with READ_TELEMETRY scope
        auth_res_ok = authorize_action(
            action="telemetry.read",
            granted_scopes=[PhonePermissionScope.READ_TELEMETRY],
            is_emergency_active=False,
        )
        self.assertTrue(auth_res_ok.allowed)

    def test_07_unauthenticated_telemetry_rejection(self):
        """7. Unauthenticated telemetry request rejection."""
        gateway = SecureGateway()
        res = gateway.handle_secure_command("{}", "127.0.0.1")
        self.assertEqual(res.code, 400)
        self.assertEqual(res.status, "DENIED")


class TestFrameProtocol(unittest.TestCase):
    """Tests 8 through 14: StreamFrame creation, validation, bounds, and clamping."""

    def test_08_frame_creation_and_checksum_computation(self):
        """8. Frame creation and checksum computation (SHA-256)."""
        payload = b"\xff\xd8\xff\xe0" + b"\x00" * 100  # synthetic JPEG header
        expected_checksum = hashlib.sha256(payload).hexdigest()

        frame = create_stream_frame(
            stream_id="stream-test-01",
            sequence_number=1,
            width=640,
            height=480,
            encoding=FrameEncoding.JPEG,
            payload_bytes=payload,
        )
        self.assertEqual(frame.checksum, expected_checksum)
        self.assertEqual(frame.compressed_size, len(payload))
        self.assertEqual(frame.width, 640)
        self.assertEqual(frame.height, 480)
        self.assertEqual(frame.encoding, "JPEG")

    def test_09_frame_dimension_boundary_enforcement(self):
        """9. Frame dimension boundary enforcement (max 1920x1080)."""
        payload = b"\x00" * 50

        # Oversized width (> 1920)
        with self.assertRaises(ValueError) as ctx:
            create_stream_frame("s1", 1, 1921, 1080, FrameEncoding.JPEG, payload)
        self.assertIn("FRAME_WIDTH_EXCEEDED", str(ctx.exception))

        # Oversized height (> 1080)
        with self.assertRaises(ValueError) as ctx:
            create_stream_frame("s1", 1, 1920, 1081, FrameEncoding.JPEG, payload)
        self.assertIn("FRAME_HEIGHT_EXCEEDED", str(ctx.exception))

        # Zero or negative width
        with self.assertRaises(ValueError) as ctx:
            create_stream_frame("s1", 1, 0, 480, FrameEncoding.JPEG, payload)
        self.assertIn("INVALID_DIMENSIONS", str(ctx.exception))

    def test_10_frame_byte_size_limit_enforcement(self):
        """10. Frame byte size limit enforcement (> 512 KB rejected)."""
        huge_payload = b"\x00" * (MAX_FRAME_BYTES + 1)
        with self.assertRaises(ValueError) as ctx:
            create_stream_frame("s1", 1, 640, 480, FrameEncoding.JPEG, huge_payload)
        self.assertIn("FRAME_SIZE_EXCEEDED", str(ctx.exception))

    def test_11_frame_encoding_whitelist_validation(self):
        """11. Frame encoding whitelist validation."""
        payload = b"\x00" * 50
        # Valid encodings
        for enc in [FrameEncoding.JPEG, FrameEncoding.PNG, FrameEncoding.WEBP, FrameEncoding.MOCK_RGB]:
            frame = create_stream_frame("s1", 1, 320, 240, enc, payload)
            self.assertEqual(frame.encoding, enc.value)

        # Invalid encoding string passed directly
        with self.assertRaises(ValueError):
            FrameEncoding("UNAUTHORIZED_RAW_BMP")

    def test_12_checksum_tampering_detection(self):
        """12. Checksum tampering detection on frame payload."""
        payload = b"\xaa\xbb\xcc\xdd" * 10
        frame = create_stream_frame("s1", 1, 320, 240, FrameEncoding.JPEG, payload)

        # Verify pristine frame passes validation
        valid, msg = validate_frame(frame)
        self.assertTrue(valid)

        # Corrupt payload
        frame.payload_bytes = b"\x00" * len(payload)
        valid, msg = validate_frame(frame)
        self.assertFalse(valid)
        self.assertIn("CHECKSUM_INTEGRITY_MISMATCH", msg)

    def test_13_frame_metadata_extraction_no_raw_pixels(self):
        """13. Frame metadata extraction (never leaking raw pixels to metadata dict)."""
        payload = b"\x12\x34\x56\x78\x9a\xbc\xde\xf0"
        frame = create_stream_frame("s1", 1, 640, 480, FrameEncoding.JPEG, payload)

        meta = frame.to_metadata_dict()
        self.assertIn("frame_id", meta)
        self.assertIn("stream_id", meta)
        self.assertIn("sequence_number", meta)
        self.assertIn("timestamp", meta)
        self.assertIn("width", meta)
        self.assertIn("height", meta)
        self.assertIn("encoding", meta)
        self.assertIn("compressed_size", meta)
        self.assertIn("checksum", meta)

        # Crucial safety check: Raw bytes or hex payloads MUST NOT be in metadata dict
        self.assertNotIn("payload_bytes", meta)
        self.assertNotIn("payload_hex", meta)

    def test_14_framerate_clamping(self):
        """14. Framerate clamping (requested 60 FPS clamped to 30, <= 0 clamped to 1)."""
        mgr = StreamManager()
        # High FPS request clamped to MAX_FRAMES_PER_SECOND (30.0)
        ok, _, s_high = mgr.create_stream("dev-fps-1", "sess-1", target_fps=60.0)
        self.assertTrue(ok)
        self.assertEqual(s_high.target_fps, MAX_FRAMES_PER_SECOND)

        # Non-positive FPS clamped to MIN_FRAMES_PER_SECOND (1.0)
        ok, _, s_low = mgr.create_stream("dev-fps-2", "sess-2", target_fps=-10.0)
        self.assertTrue(ok)
        self.assertEqual(s_low.target_fps, MIN_FRAMES_PER_SECOND)

        # Normal FPS within range (15.0) preserved
        ok, _, s_mid = mgr.create_stream("dev-fps-3", "sess-3", target_fps=15.0)
        self.assertTrue(ok)
        self.assertEqual(s_mid.target_fps, 15.0)


class TestBackpressureQueue(unittest.TestCase):
    """Tests 15 and 16: StreamBackpressureQueue drop-oldest and byte ceiling policies."""

    def test_15_backpressure_queue_drop_oldest_on_overflow(self):
        """15. Backpressure queue enqueue and drop-oldest policy on overflow."""
        queue = StreamBackpressureQueue(max_frames=3, max_bytes=100000)
        f1 = create_stream_frame("s1", 1, 320, 240, FrameEncoding.JPEG, b"frame1")
        f2 = create_stream_frame("s1", 2, 320, 240, FrameEncoding.JPEG, b"frame2")
        f3 = create_stream_frame("s1", 3, 320, 240, FrameEncoding.JPEG, b"frame3")
        f4 = create_stream_frame("s1", 4, 320, 240, FrameEncoding.JPEG, b"frame4")

        queue.enqueue(f1)
        queue.enqueue(f2)
        queue.enqueue(f3)
        self.assertEqual(queue.get_stats()["queued_frames"], 3)
        self.assertEqual(queue.get_stats()["dropped_frames"], 0)

        # Enqueueing 4th frame must drop frame 1
        dropped = queue.enqueue(f4)
        self.assertTrue(dropped)
        stats = queue.get_stats()
        self.assertEqual(stats["queued_frames"], 3)
        self.assertEqual(stats["dropped_frames"], 1)

        # Verify sequence in queue is f2, f3, f4
        d1 = queue.dequeue()
        d2 = queue.dequeue()
        d3 = queue.dequeue()
        self.assertEqual(d1.sequence_number, 2)
        self.assertEqual(d2.sequence_number, 3)
        self.assertEqual(d3.sequence_number, 4)
        self.assertIsNone(queue.dequeue())

    def test_16_backpressure_queue_byte_limit_enforcement(self):
        """16. Backpressure queue byte limit enforcement."""
        # Queue with 500 byte limit
        queue = StreamBackpressureQueue(max_frames=10, max_bytes=500)
        f1 = create_stream_frame("s1", 1, 100, 100, FrameEncoding.JPEG, b"A" * 200)
        f2 = create_stream_frame("s1", 2, 100, 100, FrameEncoding.JPEG, b"B" * 200)
        f3 = create_stream_frame("s1", 3, 100, 100, FrameEncoding.JPEG, b"C" * 200)

        queue.enqueue(f1)
        queue.enqueue(f2)
        self.assertEqual(queue.get_stats()["queued_bytes"], 400)

        # Adding f3 (200 bytes) would take total to 600 > 500.
        # Queue drops f1 (200 bytes), current_bytes becomes 400.
        queue.enqueue(f3)
        stats = queue.get_stats()
        self.assertLessEqual(stats["queued_bytes"], 500)
        self.assertEqual(stats["queued_frames"], 2)
        self.assertEqual(stats["dropped_frames"], 1)

        # Remaining frames are f2 and f3
        self.assertEqual(queue.dequeue().sequence_number, 2)
        self.assertEqual(queue.dequeue().sequence_number, 3)


class TestStreamLifecycle(unittest.TestCase):
    """Tests 17 through 27: Stream state transitions, ownership, timeouts, and emergency stop."""

    def test_17_stream_state_valid_transitions(self):
        """17. Stream state machine: valid transitions."""
        stream = StreamSession(stream_id="s1", device_id="d1", session_id="sess1")
        self.assertEqual(stream.state, StreamState.IDLE)

        # IDLE -> STARTING -> ACTIVE -> PAUSED -> ACTIVE -> STOPPING -> STOPPED
        stream.transition_to(StreamState.STARTING, "Starting stream")
        self.assertEqual(stream.state, StreamState.STARTING)

        stream.transition_to(StreamState.ACTIVE, "Stream active")
        self.assertEqual(stream.state, StreamState.ACTIVE)

        stream.transition_to(StreamState.PAUSED, "Pausing stream")
        self.assertEqual(stream.state, StreamState.PAUSED)

        stream.transition_to(StreamState.ACTIVE, "Resuming stream")
        self.assertEqual(stream.state, StreamState.ACTIVE)

        stream.transition_to(StreamState.STOPPING, "Stopping stream")
        self.assertEqual(stream.state, StreamState.STOPPING)

        stream.transition_to(StreamState.STOPPED, "Stream fully stopped")
        self.assertEqual(stream.state, StreamState.STOPPED)

        # Verify state history was logged
        self.assertGreaterEqual(len(stream.state_history), 6)

    def test_18_stream_state_invalid_transition_rejection(self):
        """18. Stream state machine: invalid transition rejection."""
        stream = StreamSession(stream_id="s1", device_id="d1", session_id="sess1")
        # IDLE -> PAUSED is illegal
        with self.assertRaises(StreamStateError):
            stream.transition_to(StreamState.PAUSED)

        # Transition to STOPPED, then attempting STOPPED -> ACTIVE is illegal
        stream.transition_to(StreamState.STOPPED)
        with self.assertRaises(StreamStateError):
            stream.transition_to(StreamState.ACTIVE)

    def test_19_stream_session_creation_and_ownership(self):
        """19. Stream session creation and device ownership enforcement."""
        mgr = StreamManager()
        ok, msg, stream = mgr.create_stream("dev-owner", "sess-owner", target_fps=10)
        self.assertTrue(ok)
        self.assertEqual(stream.device_id, "dev-owner")
        self.assertEqual(stream.session_id, "sess-owner")
        self.assertEqual(stream.state, StreamState.ACTIVE)

        # Owner can produce and retrieve frame
        mgr.produce_frame(stream.stream_id)
        f_ok, f_msg, frame = mgr.get_next_frame(stream.stream_id, "sess-owner")
        self.assertTrue(f_ok)
        self.assertIsNotNone(frame)

    def test_20_cross_device_stream_access_rejection(self):
        """20. Stream session cross-device access rejection."""
        mgr = StreamManager()
        ok, _, stream = mgr.create_stream("dev-owner", "sess-owner")
        self.assertTrue(ok)

        # Impostor session attempts to stop stream
        stop_ok, stop_msg = mgr.stop_stream(stream.stream_id, "sess-impostor")
        self.assertFalse(stop_ok)
        self.assertIn("STREAM_NOT_FOUND_OR_FORBIDDEN", stop_msg)

        # Impostor session attempts to pause stream
        pause_ok, pause_msg = mgr.pause_stream(stream.stream_id, "sess-impostor")
        self.assertFalse(pause_ok)
        self.assertIn("STREAM_NOT_FOUND_OR_FORBIDDEN", pause_msg)

        # Impostor session attempts to get frame
        frame_ok, frame_msg, _ = mgr.get_next_frame(stream.stream_id, "sess-impostor")
        self.assertFalse(frame_ok)
        self.assertTrue("STREAM_NOT_FOUND_OR_FORBIDDEN" in frame_msg or "STREAM_OWNERSHIP_MISMATCH" in frame_msg)

    def test_21_stream_inactivity_timeout(self):
        """21. Stream inactivity timeout (stream transitions to STOPPED on timeout)."""
        mgr = StreamManager()
        ok, _, stream = mgr.create_stream("dev-inactive", "sess-inactive")
        self.assertTrue(ok)

        # Simulate 35 seconds of no activity
        future_time = time.time() + STREAM_INACTIVITY_TIMEOUT_SECONDS + 5
        self.assertTrue(stream.check_inactivity(current_time=future_time))

        # Cleanup handles inactive stream
        stopped_count = mgr.cleanup_inactive_streams(current_time=future_time)
        self.assertEqual(stopped_count, 1)
        self.assertEqual(stream.state, StreamState.STOPPED)

    def test_22_stream_lifetime_expiration(self):
        """22. Stream lifetime expiration (exceeding 2h max lifetime stops stream)."""
        mgr = StreamManager()
        ok, _, stream = mgr.create_stream("dev-expire", "sess-expire")
        self.assertTrue(ok)

        future_time = time.time() + MAX_STREAM_LIFETIME_SECONDS + 10
        self.assertTrue(stream.is_expired(current_time=future_time))

        stopped_count = mgr.cleanup_inactive_streams(current_time=future_time)
        self.assertEqual(stopped_count, 1)
        self.assertEqual(stream.state, StreamState.STOPPED)

    def test_23_stream_connection_loss_and_reconnect(self):
        """23. Stream connection loss & reconnect within grace window."""
        mgr = StreamManager()
        ok, _, stream = mgr.create_stream("dev-recon", "sess-recon")
        self.assertTrue(ok)

        # Mark disconnect
        stream.record_disconnect()
        self.assertEqual(stream.state, StreamState.RECONNECTING)

        # Reconnect within window
        rec_ok, rec_msg = mgr.reconnect_stream(stream.stream_id, "sess-recon")
        self.assertTrue(rec_ok)
        self.assertEqual(stream.state, StreamState.ACTIVE)
        self.assertEqual(stream.reconnect_attempts, 1)

    def test_24_stream_reconnect_attempt_limit_exceeded(self):
        """24. Stream reconnect attempt limit exceeded -> stream terminated."""
        mgr = StreamManager()
        ok, _, stream = mgr.create_stream("dev-rec-limit", "sess-rec-limit")
        self.assertTrue(ok)

        # Max reconnect attempts allowed is MAX_RECONNECT_ATTEMPTS (3)
        for i in range(MAX_RECONNECT_ATTEMPTS):
            stream.record_disconnect()
            r_ok, _ = mgr.reconnect_stream(stream.stream_id, "sess-rec-limit")
            self.assertTrue(r_ok)

        # 4th disconnect & reconnect attempt must fail and terminate stream
        stream.record_disconnect()
        r_ok4, r_msg4 = mgr.reconnect_stream(stream.stream_id, "sess-rec-limit")
        self.assertFalse(r_ok4)
        self.assertIn("Maximum reconnect attempts exceeded", r_msg4)
        self.assertEqual(stream.state, StreamState.FAILED)

    def test_25_emergency_stop_halts_active_streams(self):
        """25. Emergency stop immediate halt: active streams transition to STOPPED immediately."""
        stop_ctrl = EmergencyStopController()
        mgr = StreamManager(emergency_stop=stop_ctrl)

        ok1, _, s1 = mgr.create_stream("d1", "sess1")
        ok2, _, s2 = mgr.create_stream("d2", "sess2")
        self.assertEqual(s1.state, StreamState.ACTIVE)
        self.assertEqual(s2.state, StreamState.ACTIVE)

        # Trigger emergency stop
        stop_ctrl.trigger("TEST_HARNESS", "Testing emergency stop stream halt")
        mgr.on_emergency_stop()

        self.assertEqual(s1.state, StreamState.STOPPED)
        self.assertEqual(s2.state, StreamState.STOPPED)
        self.assertEqual(s1.queue.get_stats()["queued_frames"], 0)

    def test_26_emergency_stop_blocks_frame_acquisition(self):
        """26. Emergency stop: frame acquisition blocked while emergency stop is active."""
        stop_ctrl = EmergencyStopController()
        mgr = StreamManager(emergency_stop=stop_ctrl)
        stop_ctrl.trigger("TEST_HARNESS", "Active emergency stop")

        # Attempt to create stream blocked
        ok, msg, stream = mgr.create_stream("d1", "sess1")
        self.assertFalse(ok)
        self.assertIn("EMERGENCY_STOP_ACTIVE", msg)

        # Attempt to produce or get frame blocked
        prod_ok, prod_msg, _ = mgr.produce_frame("non-existent")
        self.assertFalse(prod_ok)
        self.assertIn("EMERGENCY_STOP_ACTIVE", prod_msg)

    def test_27_emergency_stop_deterministic_zero_llm(self):
        """27. Emergency stop: zero LLM dependency (pure deterministic signal)."""
        stop_ctrl = EmergencyStopController()
        t0 = time.time()
        status = stop_ctrl.trigger("USER_TAP", "Immediate stop")
        t1 = time.time()

        self.assertTrue(status.is_active)
        self.assertLess(t1 - t0, 0.05)  # Must be immediate (< 50ms), zero network/LLM roundtrips
        self.assertEqual(status.triggered_by, "USER_TAP")


class TestStreamAuditAndSecurity(unittest.TestCase):
    """Tests 28 through 35: Audit logging, redaction, isolation, and localhost binding."""

    def test_28_audit_logging_stream_lifecycle_metadata(self):
        """28. Audit logging: stream start/stop/pause/resume logged with metadata."""
        gateway = SecureGateway()
        code = gateway.pairing_manager.generate_pairing_code()
        _, _, secret = gateway.pairing_manager.confirm_pairing("dev-audit-01", code)
        _, _, session = gateway.session_manager.create_session(
            "dev-audit-01",
            scopes=[PhonePermissionScope.READ_SCREEN_STREAM, PhonePermissionScope.SEND_COMMAND],
        )

        import secrets
        def make_req(action, payload):
            nonce_val = f"nonce-{action}-{secrets.token_hex(8)}"
            req = SecureRequest(
                request_id=f"req-{action}-{secrets.token_hex(6)}",
                device_id="dev-audit-01",
                session_id=session.session_id,
                action=action,
                payload=payload,
                scope=PhonePermissionScope.READ_SCREEN_STREAM.value,
                timestamp=time.time(),
                nonce=nonce_val,
            )
            req.sign(secret)
            return req

        # 1. Start stream
        start_res = gateway.handle_secure_command(
            make_req("stream.start", {"target_fps": 10, "max_width": 640, "max_height": 480}).to_json(),
            "127.0.0.1",
        )
        self.assertEqual(start_res.code, 200)
        stream_id = start_res.data["stream_id"]

        # 2. Pause stream
        pause_res = gateway.handle_secure_command(
            make_req("stream.pause", {"stream_id": stream_id}).to_json(),
            "127.0.0.1",
        )
        self.assertEqual(pause_res.code, 200)

        # 3. Resume stream
        res_res = gateway.handle_secure_command(
            make_req("stream.resume", {"stream_id": stream_id}).to_json(),
            "127.0.0.1",
        )
        self.assertEqual(res_res.code, 200)

        # 4. Stop stream
        stop_res = gateway.handle_secure_command(
            make_req("stream.stop", {"stream_id": stream_id}).to_json(),
            "127.0.0.1",
        )
        self.assertEqual(stop_res.code, 200)

        # Verify audit records exist for all stream commands
        actions_logged = [r.action for r in gateway.audit_logger.get_records()]
        self.assertIn("stream.start", actions_logged)
        self.assertIn("stream.pause", actions_logged)
        self.assertIn("stream.resume", actions_logged)
        self.assertIn("stream.stop", actions_logged)

    def test_29_audit_logging_telemetry_redaction(self):
        """29. Audit logging: telemetry reads logged without leaking sensitive payloads."""
        data_to_redact = {
            "api_key": "secret-12345",
            "session_token": "token-67890",
            "private_key": "key-abcdef",
            "safe_metric": "fps_value_10",
        }
        redacted = redact_sensitive_data(data_to_redact)
        self.assertEqual(redacted["api_key"], "[REDACTED]")
        self.assertEqual(redacted["session_token"], "[REDACTED]")
        self.assertEqual(redacted["private_key"], "[REDACTED]")
        self.assertEqual(redacted["safe_metric"], "fps_value_10")

    def test_30_pixel_data_strictly_excluded_from_audit(self):
        """30. Audit logging: pixel data strictly excluded from all audit records."""
        gateway = SecureGateway()
        logger = gateway.audit_logger

        frame = create_stream_frame("s1", 1, 320, 240, FrameEncoding.JPEG, b"\xff\xd8\xff\xe0" * 20)
        logger.log_event(
            event_type="FRAME_CAPTURED",
            result="SUCCESS",
            device_id="dev-test",
            session_id="sess-test",
            metadata=frame.to_metadata_dict(),
        )

        for record in logger.get_records():
            meta = record.metadata
            self.assertNotIn("payload_bytes", meta)
            self.assertNotIn("payload_hex", meta)
            self.assertNotIn("pixels", meta)

    def test_31_model_isolation_blocks_screen_capture_and_stream(self):
        """31. Model isolation: LLM blocked from calling screen capture or stream methods directly."""
        gate = ModelIsolationGate()
        blocked_actions = [
            "screen_capture",
            "stream_start",
            "stream_stop",
            "stream_frame",
            "capture_screen",
            "read_screen_pixels",
        ]
        for action in blocked_actions:
            allowed, reason = gate.is_model_authorized(action)
            self.assertFalse(allowed)
            self.assertIn("Prohibited action", reason)

    def test_32_model_isolation_blocks_raw_sockets_and_queues(self):
        """32. Model isolation: LLM cannot access raw streaming sockets or queues."""
        gate = ModelIsolationGate()
        for resource in ["socket", "queue", "adb", "shell", "powershell"]:
            allowed, _ = gate.is_model_authorized(f"access_{resource}")
            self.assertFalse(allowed)

    def test_33_observation_only_blocks_remote_control_actions(self):
        """33. Observation-only boundary: remote control actions rejected with 403 Forbidden."""
        prohibited_remote_actions = [
            "remote.click",
            "remote.type",
            "remote.mouse_move",
            "remote.key_press",
            "remote.input",
            "mouse_click",
            "key_event",
            "desktop_control",
        ]
        companion_scopes = [PhonePermissionScope.READ_SCREEN_STREAM, PhonePermissionScope.READ_TELEMETRY]

        for action in prohibited_remote_actions:
            auth_res = authorize_action(action, granted_scopes=companion_scopes, is_emergency_active=False)
            self.assertFalse(auth_res.allowed)
            self.assertIn(auth_res.decision_code, ("PROHIBITED_ACTION", "SCOPE_DENIED"))

    def test_34_zero_shell_true_across_phase2_modules(self):
        """34. Zero shell=True subprocesses across all Phase 2 streaming modules."""
        phase2_modules = [
            Path(__file__).resolve().parent.parent / "app" / "remote" / "telemetry.py",
            Path(__file__).resolve().parent.parent / "app" / "remote" / "frame.py",
            Path(__file__).resolve().parent.parent / "app" / "remote" / "screen_capture.py",
            Path(__file__).resolve().parent.parent / "app" / "remote" / "stream.py",
            Path(__file__).resolve().parent.parent / "app" / "remote" / "server.py",
            Path(__file__).resolve().parent.parent / "app" / "remote" / "permissions.py",
            Path(__file__).resolve().parent.parent / "app" / "remote" / "config.py",
        ]

        for mod_path in phase2_modules:
            self.assertTrue(mod_path.exists(), f"File missing: {mod_path}")
            source = mod_path.read_text(encoding="utf-8")
            tree = ast.parse(source)

            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    # Check keywords for shell=True
                    for kw in node.keywords:
                        if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                            self.fail(f"Violation: shell=True found in {mod_path.name}:{node.lineno}")

    def test_35_streaming_server_localhost_only_no_public_bind(self):
        """35. Localhost binding only: streaming server cannot bind to 0.0.0.0."""
        gateway = SecureGateway()
        with self.assertRaises(SecurityBindingError):
            SecureDashboardServer(gateway=gateway, host="0.0.0.0")

        with self.assertRaises(SecurityBindingError):
            SecureDashboardServer(gateway=gateway, host="192.168.1.100")

        # Valid localhost configurations
        server1 = SecureDashboardServer(gateway=gateway, host="127.0.0.1")
        self.assertEqual(server1.host, "127.0.0.1")
        server2 = SecureDashboardServer(gateway=gateway, host="localhost")
        self.assertEqual(server2.host, "localhost")


class TestCaptureEnginesAndLimits(unittest.TestCase):
    """Tests 36 through 40: Screen capture engines, concurrency bounds, and backward compatibility."""

    def test_36_mock_screen_capture_engine_label_and_frames(self):
        """36. Mock screen capture engine generates valid synthetic frames with DEVELOPMENT_MOCK_CAPTURE label."""
        engine = MockScreenCaptureEngine(default_width=640, default_height=480)
        self.assertEqual(engine.get_capture_type(), "DEVELOPMENT_MOCK_CAPTURE")

        ok, msg, frame = engine.capture_frame(
            stream_id="mock-stream-01",
            sequence_number=1,
            max_width=640,
            max_height=480,
            encoding=FrameEncoding.JPEG,
        )
        self.assertTrue(ok)
        self.assertIsNotNone(frame)
        self.assertEqual(frame.width, 640)
        self.assertEqual(frame.height, 480)
        self.assertEqual(frame.encoding, "JPEG")
        self.assertGreater(frame.compressed_size, 0)
        # Validate checksum
        v_ok, _ = validate_frame(frame)
        self.assertTrue(v_ok)

    def test_37_local_screen_capture_engine_headless_fallback(self):
        """37. Local screen capture engine handles headless environment gracefully."""
        engine = LocalScreenCaptureEngine()
        self.assertEqual(engine.get_capture_type(), "LOCAL_SCREEN_CAPTURE")

        # Attempt capture; must not crash even in headless/CI
        ok, msg, frame = engine.capture_frame("local-stream-01", 1, 640, 480, FrameEncoding.JPEG)
        if ok:
            self.assertIsNotNone(frame)
            self.assertEqual(frame.encoding, "JPEG")
        else:
            self.assertTrue("LOCAL_CAPTURE_FAILED" in msg or "HEADLESS" in msg or "PIL_NOT_AVAILABLE" in msg)
            self.assertIsNone(frame)

    def test_38_max_concurrent_streams_per_device_limit(self):
        """38. Maximum concurrent streams per device limit enforcement."""
        mgr = StreamManager()
        ok1, _, s1 = mgr.create_stream("device-single", "session-1")
        self.assertTrue(ok1)

        # Attempt to create second stream for same device
        ok2, msg2, s2 = mgr.create_stream("device-single", "session-1")
        self.assertFalse(ok2)
        self.assertIn("already has an active stream", msg2)
        self.assertIsNone(s2)

    def test_39_max_global_concurrent_streams_limit(self):
        """39. Maximum global concurrent streams limit enforcement."""
        mgr = StreamManager()
        # Create up to MAX_GLOBAL_CONCURRENT_STREAMS (5)
        for i in range(MAX_GLOBAL_CONCURRENT_STREAMS):
            ok, _, _ = mgr.create_stream(f"dev-global-{i}", f"sess-global-{i}")
            self.assertTrue(ok)

        # 6th stream exceeds global limit
        ok_overflow, msg_overflow, s_overflow = mgr.create_stream("dev-overflow", "sess-overflow")
        self.assertFalse(ok_overflow)
        self.assertIn("GLOBAL_STREAM_LIMIT_EXCEEDED", msg_overflow)
        self.assertIsNone(s_overflow)

    def test_40_backward_compatibility_with_phase1_endpoints(self):
        """40. Backward compatibility with Step 10 Phase 1 endpoints."""
        gateway = SecureGateway()
        # Test Phase 1 pairing and authentication workflows
        code = gateway.pairing_manager.generate_pairing_code()
        ok, msg, secret = gateway.pairing_manager.confirm_pairing("dev-p1-compat", code)
        self.assertTrue(ok)

        auth_res = gateway.handle_auth_request(
            {
                "device_id": "dev-p1-compat",
                "device_secret": secret,
                "ttl_seconds": 3600,
            },
            client_ip="127.0.0.1",
        )
        self.assertEqual(auth_res.code, 200)
        session_id = auth_res.data["session_id"]
        session_key = bytes.fromhex(auth_res.data["session_key_hex"])

        # Status read request using signed SecureRequest
        req = SecureRequest(
            request_id="req-compat-01",
            device_id="dev-p1-compat",
            session_id=session_id,
            action="status.read",
            payload={},
            scope=PhonePermissionScope.READ_STATUS.value,
            timestamp=time.time(),
            nonce="nonce-compat-01",
        )
        req.sign(secret)

        res = gateway.handle_secure_command(req.to_json(), "127.0.0.1")
        self.assertEqual(res.code, 200)
        self.assertIn("status", res.data)


if __name__ == "__main__":
    unittest.main()
