from pathlib import Path
from unittest.mock import patch
from streamlit.testing.v1 import AppTest

SCRIPT = Path(__file__).resolve().parents[1] / 'src/ui/dashboard.py'


def backend(method, path, **kwargs):
    if path.endswith('/status'):
        if '/voice/' in path:
            return {'ready': True}
        return {'is_running': True, 'active_fire_tv_clients': 1}
    if path.endswith('/history'):
        return {'events': []}
    if method == 'POST':
        return {'rule': {'id': 'carrying', 'source': 'person'}, 'monitoring_running': True}
    return {'rules': []}


def test_typed_command_and_preset_use_same_creation_flow():
    with patch('src.ui.client.DashboardClient.request', side_effect=backend) as request:
        ui = AppTest.from_file(str(SCRIPT)).run(timeout=15)
        assert not ui.exception
        next(button for button in ui.button if button.label == 'Person with a box').click().run()
        assert ui.text_area[0].value == 'Alert me if a person is carrying a box'
        next(button for button in ui.button if button.label == 'Create alert').click().run()
        assert not ui.exception
        assert ui.success[0].value == 'Your alert is ready.'
        calls = [call for call in request.call_args_list if call.args == ('POST', '/api/v1/automations/rules')]
        assert len(calls) == 1
        assert calls[0].kwargs['json']['prompt'] == 'Alert me if a person is carrying a box'
        assert ui.text_area[0].value == ''


def test_backend_outage_does_not_claim_to_arm_rules():
    with patch('src.ui.client.DashboardClient.request', side_effect=RuntimeError('Offline')):
        ui = AppTest.from_file(str(SCRIPT)).run(timeout=15)
    assert not ui.exception
    assert next(button for button in ui.button if button.label == 'Create alert').disabled
