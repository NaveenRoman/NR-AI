import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.agent.computer_agent import (
    MAX_RETRIES_PER_STEP,
    MAX_STEPS_PER_WORKFLOW,
    TARGET_TTL_SECONDS,
    UnifiedComputerAgent,
    WorkflowReport,
    WorkflowStep,
)
from app.agent.computer_tools import (
    ComputerToolRegistry,
    RiskLevel,
    ToolExecutionResult,
)
from app.agent.input_controller import InputActionResult, InputController
from app.agent.safe_action_dispatcher import SafeActionDispatcher
from app.agent.state_verifier import StateVerifier
from app.agent.window_manager import WindowManager
from app.brain.companion import CommandCategory, NRCompanion
from app.commands.app_launcher import AppLauncher
from app.config.voice_config import VoiceConfig
from app.memory.audit_logger import AuditLogger
from app.memory.context_memory import ProjectContextMemory
from app.vision.vision_service import ScreenVisionService


class TestStep4EUnifiedComputerAgent(unittest.TestCase):
    def setUp(self):
        self.audit = AuditLogger(log_dir=str(REPO_ROOT / 'data' / 'audit_test'))
        self.memory = ProjectContextMemory(workspace=str(REPO_ROOT))
        self.window_mgr = WindowManager()
        self.vision_svc = ScreenVisionService(window_manager=self.window_mgr)
        self.input_ctrl = InputController(
            window_manager=self.window_mgr,
            vision_service=self.vision_svc,
            audit_logger=self.audit,
        )
        self.app_launcher = AppLauncher()
        self.state_verifier = StateVerifier()
        self.tools = ComputerToolRegistry(
            app_launcher=self.app_launcher,
            window_manager=self.window_mgr,
            vision_service=self.vision_svc,
            input_controller=self.input_ctrl,
            state_verifier=self.state_verifier,
            audit_logger=self.audit,
        )
        self.agent = UnifiedComputerAgent(
            tool_registry=self.tools,
            memory=self.memory,
            audit_logger=self.audit,
            window_manager=self.window_mgr,
            vision_service=self.vision_svc,
            input_controller=self.input_ctrl,
        )
        self.input_ctrl.reset_emergency_stop()

    def tearDown(self):
        self.input_ctrl.reset_emergency_stop()

    def test_A_tool_registry_validation(self):
        expected_tools = [
            'computer.open_app',
            'computer.list_windows',
            'computer.focus_window',
            'computer.inspect_screen',
            'computer.find_text',
            'computer.click_target',
            'computer.double_click',
            'computer.type_text',
            'computer.press_key',
            'computer.hotkey',
            'computer.scroll',
            'computer.verify',
        ]
        registered = [t['name'] for t in self.tools.list_tools()]
        for exp in expected_tools:
            self.assertIn(exp, registered, f'Missing tool in registry: {exp}')
            tool_def = self.tools.get_tool(exp)
            self.assertIsNotNone(tool_def)
            self.assertTrue(callable(tool_def.handler))

    def test_B_invalid_tool_rejection(self):
        res = self.tools.execute_tool('computer.format_hard_drive', {'target': 'C:'})
        self.assertFalse(res.success)
        self.assertEqual(res.error, 'INVALID_TOOL')
        self.assertIn('not in the approved', res.message)

    def test_C_single_step_execution(self):
        step = WorkflowStep(1, 'computer.list_windows', {})
        res = self.agent.execute_step(step)
        self.assertTrue(res.success)
        self.assertEqual(res.tool, 'computer.list_windows')
        self.assertIn('windows', res.data)
        self.assertEqual(step.status, 'COMPLETED')

    def test_D_multi_step_execution(self):
        goal = 'Open Chrome and search for Android Studio.'
        ok, steps, err = self.agent.plan_workflow(goal)
        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertEqual(len(steps), 5)
        self.assertEqual(steps[0].tool, 'computer.open_app')
        self.assertEqual(steps[1].tool, 'computer.focus_window')
        self.assertEqual(steps[2].tool, 'computer.type_text')
        self.assertEqual(steps[3].tool, 'computer.press_key')
        self.assertEqual(steps[4].tool, 'computer.verify')

    def test_E_app_launch_integration(self):
        tool_def = self.tools.get_tool('computer.open_app')
        self.assertIn('app_name', tool_def.required_params)

        res_unauth = self.tools.execute_tool('computer.open_app', {'app_name': 'unauthorized_malware.exe'})
        self.assertFalse(res_unauth.success)
        self.assertEqual(res_unauth.error, 'UNAUTHORIZED_APPLICATION')

        with patch.object(self.app_launcher, 'launch_detailed', return_value={'success': True, 'path': 'notepad.exe', 'message': 'Notepad is opening.'}):
            res_auth = self.tools.execute_tool('computer.open_app', {'app_name': 'notepad'})
            self.assertTrue(res_auth.success)
            self.assertEqual(res_auth.tool, 'computer.open_app')

    def test_F_window_activation_integration(self):
        with patch.object(self.window_mgr, 'activate_window', return_value=(True, 'Window brought to foreground.')):
            res = self.tools.execute_tool('computer.focus_window', {'target': 'notepad'})
            self.assertTrue(res.success)
            self.assertTrue(res.verified)

    def test_G_vision_integration(self):
        mock_ocr = [{'text': 'File', 'confidence': 0.95, 'box': [[10, 10], [50, 10], [50, 30], [10, 30]]}]
        with patch.object(self.vision_svc.screen_vision, 'read_screen', return_value=mock_ocr):
            res = self.tools.execute_tool('computer.inspect_screen', {})
            self.assertTrue(res.success)
            self.assertIn('File', str(res.data))

    def test_H_target_resolution(self):
        mock_find = {
            'found': True,
            'match': {'text': 'Submit', 'confidence': 0.92, 'center': (300, 400)},
            'items': [{'text': 'Submit', 'confidence': 0.92, 'box': [[280, 390], [320, 390], [320, 410], [280, 410]]}],
        }
        with patch.object(self.vision_svc, 'find_text', return_value=mock_find):
            ok, coords, err = self.agent.resolve_visual_target('Submit')
            self.assertTrue(ok)
            self.assertEqual(coords, (300, 400))
            self.assertIsNone(err)

    def test_I_target_freshness(self):
        now = time.time()
        fresh_cache = {'timestamp': now - 5.0, 'center': (150, 250), 'window': 'Chrome'}
        ok, coords, err = self.agent.resolve_visual_target('Search', cached_target=fresh_cache)
        self.assertTrue(ok)
        self.assertEqual(coords, (150, 250))

        stale_cache = {'timestamp': now - 20.0, 'center': (150, 250), 'window': 'Chrome'}
        with patch.object(self.vision_svc, 'find_text', return_value={'found': False}):
            ok_stale, coords_stale, err_stale = self.agent.resolve_visual_target('Search', cached_target=stale_cache)
            self.assertFalse(ok_stale)
            self.assertIsNone(coords_stale)
            self.assertIn('could not be located', err_stale)

    def test_J_target_re_resolution(self):
        now = time.time()
        stale_cache = {'timestamp': now - 30.0, 'center': (100, 100), 'window': 'Chrome'}
        mock_fresh_find = {
            'found': True,
            'match': {'text': 'OK', 'confidence': 0.90, 'center': (450, 550)},
            'items': [{'text': 'OK', 'confidence': 0.90, 'box': [[440, 540], [460, 540], [460, 560], [440, 560]]}],
        }
        with patch.object(self.vision_svc, 'find_text', return_value=mock_fresh_find):
            ok, coords, err = self.agent.resolve_visual_target('OK', cached_target=stale_cache)
            self.assertTrue(ok)
            self.assertEqual(coords, (450, 550))

    def test_K_ambiguous_target_rejection(self):
        mock_ambiguous = {
            'found': True,
            'match': {'text': 'Save', 'confidence': 0.88, 'center': (100, 100)},
            'items': [
                {'text': 'Save', 'confidence': 0.88, 'box': [[90, 90], [110, 90], [110, 110], [90, 110]]},
                {'text': 'Save', 'confidence': 0.89, 'box': [[500, 500], [520, 500], [520, 520], [500, 520]]},
            ],
        }
        with patch.object(self.vision_svc, 'find_text', return_value=mock_ambiguous):
            ok, coords, err = self.agent.resolve_visual_target('Save')
            self.assertFalse(ok)
            self.assertIsNone(coords)
            self.assertIn('Ambiguous target', err)

    def test_L_click_verification(self):
        with patch.object(self.input_ctrl, 'click_target', return_value=InputActionResult(success=True, action='click', coordinates=(200, 300), verified=True, message='Clicked target at (200, 300).')):
            res = self.tools.execute_tool('computer.click_target', {'coordinates': (200, 300)})
            self.assertTrue(res.success)
            self.assertTrue(res.verified)

    def test_M_keyboard_verification(self):
        with patch.object(self.input_ctrl, 'type_text', return_value=InputActionResult(success=True, action='type_text', verified=True, message='Typed text into active window.')):
            res_t = self.tools.execute_tool('computer.type_text', {'text': 'Hello NR AI'})
            self.assertTrue(res_t.success)
            self.assertTrue(res_t.verified)

        with patch.object(self.input_ctrl, 'press_key', return_value=InputActionResult(success=True, action='press_key', verified=True, message='Pressed key enter.')):
            res_k = self.tools.execute_tool('computer.press_key', {'key': 'enter'})
            self.assertTrue(res_k.success)
            self.assertTrue(res_k.verified)

    def test_N_window_disappearance_abort(self):
        with patch.object(self.input_ctrl, 'validate_target_window', return_value=(False, 'Target window Notepad not found on desktop.')):
            step = WorkflowStep(1, 'computer.click_target', {'coordinates': (100, 100), 'target_window': 'Notepad'})
            res = self.agent.execute_step(step)
            self.assertFalse(res.success)
            self.assertEqual(res.error, 'WINDOW_DISAPPEARED')
            self.assertIn('Window safety abort', res.message)

    def test_O_emergency_stop_during_workflow(self):
        self.input_ctrl.emergency_stop()
        self.assertTrue(self.input_ctrl.is_emergency_stopped())

        step = WorkflowStep(1, 'computer.click_target', {'coordinates': (100, 100)})
        res = self.agent.execute_step(step)
        self.assertFalse(res.success)
        self.assertEqual(res.error, 'EMERGENCY_STOP_ACTIVE')

        report = self.agent.execute_workflow('Open Chrome and search for Android Studio.')
        self.assertFalse(report.success)
        self.assertEqual(report.error, 'EMERGENCY_STOP_ACTIVE')

    def test_P_maximum_10_step_enforcement(self):
        too_many_steps = [
            WorkflowStep(i, 'computer.press_key', {'key': 'down'})
            for i in range(1, 12)
        ]
        self.assertEqual(MAX_STEPS_PER_WORKFLOW, 10)
        self.assertGreater(len(too_many_steps), MAX_STEPS_PER_WORKFLOW)

        with patch.object(self.agent, 'plan_workflow', return_value=(False, [], 'Workflow exceeds maximum step limit (11 > 10). Action aborted to prevent runaway execution.')):
            report = self.agent.execute_workflow('Do 11 actions')
            self.assertFalse(report.success)
            self.assertEqual(report.error, 'PLANNING_FAILED')
            self.assertIn('exceeds maximum step limit', report.summary)

    def test_Q_maximum_2_retries(self):
        self.assertEqual(MAX_RETRIES_PER_STEP, 2)
        fail_res = ToolExecutionResult(success=False, tool='computer.click_target', error='TARGET_NOT_FOUND', message='Could not locate visual target.')
        step = WorkflowStep(1, 'computer.click_target', {'target_name': 'MissingButton'})
        with patch.object(self.vision_svc, 'find_text', return_value={'found': True, 'match': {'center': (100, 100)}}):
            with patch.object(self.tools, 'execute_tool', return_value=fail_res):
                res = self.agent.execute_step(step)
                self.assertFalse(res.success)
                self.assertEqual(step.retry_count, 2)
                self.assertEqual(step.status, 'FAILED')

    def test_R_rate_limiting(self):
        self.assertTrue(self.input_ctrl.min_action_delay >= 0.05)
        self.assertTrue(self.input_ctrl.max_actions_per_sequence > 0)

    def test_S_dangerous_action_rejection(self):
        dangerous_calls = [
            ('computer.type_text', {'text': 'del /s /q C:\\'}),
            ('computer.type_text', {'text': 'rmdir /s /q C:\\Windows'}),
            ('computer.type_text', {'text': 'format D:'}),
            ('computer.type_text', {'text': 'shutdown /s /t 0'}),
        ]
        for t_name, p in dangerous_calls:
            risk, reason = self.tools.assess_risk(t_name, p)
            self.assertEqual(risk, RiskLevel.HIGH)
            self.assertIsNotNone(reason)

    def test_T_high_risk_confirmation_boundary(self):
        params = {'text': 'format C:'}
        res_unconfirmed = self.tools.execute_tool('computer.type_text', params, user_confirmed=False)
        self.assertFalse(res_unconfirmed.success)
        self.assertTrue(res_unconfirmed.requires_confirmation)
        self.assertEqual(res_unconfirmed.error, 'CONFIRMATION_REQUIRED')

        with patch.object(self.input_ctrl, 'type_text', return_value=InputActionResult(success=True, action='type_text', message='Executed.')):
            res_confirmed = self.tools.execute_tool('computer.type_text', params, user_confirmed=True)
            self.assertTrue(res_confirmed.success)

    def test_U_sensitive_data_redaction(self):
        sensitive_params = {'text': 'SuperSecretPassword123!', 'is_sensitive': True}
        redacted = self.tools._redact_params(sensitive_params)
        self.assertEqual(redacted['text'], '[REDACTED]')

        token_params = {'text': 'api_token_xyz987', 'is_sensitive': False}
        redacted_token = self.tools._redact_params(token_params)
        self.assertEqual(redacted_token['text'], '[REDACTED]')

    def test_V_no_arbitrary_shell_execution(self):
        res = self.tools.execute_tool('computer.run_shell', {'cmd': 'dir | findstr py'})
        self.assertFalse(res.success)
        self.assertEqual(res.error, 'INVALID_TOOL')

    def test_W_no_arbitrary_executable_execution(self):
        res = self.tools.execute_tool('computer.open_app', {'app_name': 'mimikatz.exe'})
        self.assertFalse(res.success)
        self.assertEqual(res.error, 'UNAUTHORIZED_APPLICATION')

    def test_X_audit_logging(self):
        step = WorkflowStep(1, 'computer.list_windows', {})
        res = self.agent.execute_step(step, workflow_id='wf_audit_test')
        self.assertTrue(res.success)

        events = self.audit.get_entries()
        tool_events = [e for e in events if e.get('event_type') in ('computer_tool_execution', 'computer_workflow_step')]
        self.assertTrue(len(tool_events) > 0)

    def test_Y_context_memory(self):
        self.memory.set_active_window({'title': 'Google Chrome', 'process_name': 'chrome.exe'})
        self.assertEqual(self.memory.get_active_window()['title'], 'Google Chrome')

        self.memory.set_active_visual_target('Search Button', (250, 350), window='Chrome')
        target = self.memory.get_active_visual_target(max_age_seconds=15.0)
        self.assertIsNotNone(target)
        self.assertEqual(target['target'], 'Search Button')
        self.assertEqual(target['coordinates'], [250, 350])

        self.memory.set_last_action({'tool': 'computer.click_target', 'success': True})
        self.assertEqual(self.memory.get_last_action()['tool'], 'computer.click_target')

        self.memory.set_verification_result({'verified': True, 'text': 'Verified State'})
        self.assertTrue(self.memory.get_verification_result()['verified'])

        self.memory.set_workflow_state({'workflow_id': 'wf_1', 'status': 'RUNNING'})
        self.assertEqual(self.memory.get_workflow_state()['status'], 'RUNNING')

    def test_Z_step1_conversation_memory_regression(self):
        self.memory.record_command('open notepad')
        self.assertEqual(self.memory.recent_commands_executed[0], 'open notepad')
        self.memory.record_file_modified('C:/NR-AI/test.py')
        self.assertEqual(self.memory.get_last_modified_file(), 'C:/NR-AI/test.py')

    def test_AA_step2_active_target_memory_regression(self):
        self.memory.set_active_target('C:/NR-AI/hello.py', target_type='file')
        self.assertEqual(self.memory.get_active_target_value(), 'C:/NR-AI/hello.py')
        resolved = self.memory.resolve_followup_target('open it')
        self.assertEqual(resolved, 'C:/NR-AI/hello.py')

    def test_AB_step3_safe_actions_regression(self):
        dispatcher = SafeActionDispatcher()
        res = dispatcher.execute_action('open', {'value': 'ftp://example.com/exploit', 'type': 'url'})
        self.assertFalse(res.success)
        self.assertEqual(res.error, 'UNSAFE_URL_SCHEME')

    def test_AC_step4a_app_launch_regression(self):
        launcher = AppLauncher()
        self.assertIn('notepad', launcher.approved_applications)
        self.assertIn('chrome', launcher.approved_applications)
        res_unauth = launcher.launch_detailed('mimikatz')
        self.assertFalse(res_unauth['success'])
        self.assertEqual(res_unauth['error'], 'UNAUTHORIZED_APPLICATION')

    def test_AD_step4b_window_management_regression(self):
        wm = WindowManager()
        windows = wm.get_windows(include_cloaked=False)
        self.assertIsInstance(windows, list)

    def test_AE_step4c_screen_vision_regression(self):
        vs = ScreenVisionService(window_manager=self.window_mgr)
        self.assertIsNotNone(vs.screen_vision)
        self.assertIsNotNone(vs.window_vision)

    def test_AF_step4d_keyboard_mouse_control_regression(self):
        ic = InputController(window_manager=self.window_mgr, vision_service=self.vision_svc)
        val_ok, val_err, _ = ic.validate_coordinates(-100, -200)
        self.assertFalse(val_ok)
        self.assertIn('negative', val_err.lower())

        val_ok2, val_err2, _ = ic.validate_coordinates(99999, 99999)
        self.assertFalse(val_ok2)
        self.assertIn('exceed screen bounds', val_err2.lower())

        res_k = ic.press_key('dangerous_key_combo')
        self.assertFalse(res_k.success)
        self.assertEqual(res_k.error, 'UNAPPROVED_KEY')

        ic.emergency_stop()
        self.assertTrue(ic.is_emergency_stopped())
        res_stopped = ic.click_target(x=100, y=100)
        self.assertFalse(res_stopped.success)
        self.assertEqual(res_stopped.error, 'EMERGENCY_STOP_ACTIVE')
        ic.reset_emergency_stop()


if __name__ == '__main__':
    unittest.main()
