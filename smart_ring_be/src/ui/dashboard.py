"""Alexa dashboard: one command composer, a watchlist, and recent activity."""
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
import streamlit as st
from src.ui.client import DashboardClient

load_dotenv(ROOT / '.env')
st.set_page_config(page_title='Alexa · Smart Ring', page_icon='◉', layout='wide', initial_sidebar_state='collapsed')
client = DashboardClient()

st.markdown('''<style>
:root {color-scheme:light;}
.stApp {background:#f5f6f8;color:#17232e;}
.stMainBlockContainer {max-width:1160px;padding-top:2.8rem;padding-bottom:3rem;}
header[data-testid="stHeader"] {background:transparent;}
[data-testid="stToolbar"] {display:none;}
h1,h2,h3 {font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;letter-spacing:-.04em;color:#17232e;}
h1 {font-size:2.65rem!important;line-height:1.15!important;font-weight:650!important;}
h3 {font-size:1.2rem!important;font-weight:600!important;}
p, label {color:#344450;}
[data-testid="stCaptionContainer"] p {color:#71808c;font-size:.83rem;}
[data-testid="stVerticalBlockBorderWrapper"] {background:white;border-radius:18px!important;border-color:#e4e8eb!important;}
.st-key-composer_card,.st-key-watchlist_card,.st-key-connection_card {background:white;border-radius:18px!important;border-color:#e4e8eb!important;}
.stButton>button,.stFormSubmitButton>button {border-radius:10px;box-shadow:none;font-weight:500;background:white;color:#344450;border-color:#dce3e6;}
button p {color:inherit;}
button[kind="primary"],button[kind="primaryFormSubmit"] {background:#246b5a;border:1px solid #246b5a;color:white;}
textarea {background:#fafbfc!important;color:#17232e!important;border-radius:12px!important;font-size:1.05rem!important;}
[data-testid="stMetricValue"] {font-size:1.5rem;}
.brand {font-size:17px;font-weight:650;letter-spacing:-.03em;color:#192b35;}
.brand small {color:#87939c;font-weight:400;}
.eyebrow {font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:#7a8892;margin:20px 0 10px;}
.status {font-size:12px;color:#61716e;text-align:right;padding-top:4px;}
.dot {display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:7px;background:#2f896d;}
.dot.off {background:#c48f40;}
.hero-note {max-width:490px;color:#74808b;font-size:16px;line-height:1.7;margin-bottom:28px;}
hr {border-color:#e3e7ea;}
@media(max-width:700px) {.stMainBlockContainer {padding:1.5rem 1rem;}h1{font-size:2rem!important;}}
</style>''', unsafe_allow_html=True)

for key, default in [('command', ''), ('recent_commands', []), ('audio_generation', 0)]:
    if key not in st.session_state:
        st.session_state[key] = default
if st.session_state.pop('clear_composer', False):
    st.session_state.command = ''
    st.session_state.audio_generation += 1


def optional(path, default=None):
    try:
        return client.request('GET', path)
    except RuntimeError:
        return default


def fill_command(text):
    st.session_state.command = text


status = optional('/api/v1/automations/status')
voice = optional('/api/v1/voice/status', {})
entries = optional('/api/v1/automations/rules', {}).get('rules', [])
active = sum(entry.get('enabled', True) for entry in entries)
connected = status is not None
running = connected and status.get('is_running', False)

brand, connection = st.columns([2, 1])
with brand:
    st.markdown('<div class="brand">◉ &nbsp; Smart Ring <small> / Alexa</small></div>', unsafe_allow_html=True)
with connection:
    label = 'Monitoring is running' if running else 'Monitoring is paused' if connected else 'Server unavailable'
    dot = '' if running else ' off'
    st.markdown(f'<div class="status"><span class="dot{dot}"></span>{label}</div>', unsafe_allow_html=True)
st.markdown('<div class="eyebrow">Your home assistant</div>', unsafe_allow_html=True)
st.title('A little more peace of mind.')
st.markdown('<div class="hero-note">Tell your home what to watch for. Speak a command or type it below, and we’ll take it from there.</div>', unsafe_allow_html=True)

if st.session_state.get('notice'):
    kind, message = st.session_state.pop('notice')
    getattr(st, kind)(message)

composer, watchlist = st.columns([1.6, 1], gap='large')
with composer:
    with st.container(border=True, key='composer_card'):
        st.subheader('What should I watch for?')
        st.caption('Try a suggestion, or use your own words.')
        suggestions = [
            ('Person with a box', 'Alert me if a person is carrying a box'),
            ('Package removed', 'Alert me whenever a package disappears'),
            ('White vehicle', 'Alert me if a white car is detected'),
        ]
        chips = st.columns(3)
        for column, (label, prompt) in zip(chips, suggestions):
            column.button(label, on_click=fill_command, args=(prompt,), use_container_width=True)

        with st.expander('Use your voice', expanded=bool(voice.get('ready'))):
            st.caption('Record, stop, then review your words below. Recording alone does not create an alert.')
            if not voice.get('ready'):
                st.info(voice.get('detail', 'Voice is unavailable until the updated home server is running.'))
            recording = st.audio_input('Record a command', sample_rate=16000,
                                       key=f'recording_{st.session_state.audio_generation}',
                                       disabled=not voice.get('ready', False))
            if recording is not None:
                audio = recording.getvalue()
                fingerprint = hashlib.sha256(audio).hexdigest()
                if st.session_state.get('processed_audio') != fingerprint:
                    st.session_state.processed_audio = fingerprint
                    try:
                        with st.spinner('Listening to your recording…'):
                            transcript = client.transcribe(audio)
                        st.session_state.command = transcript['text']
                        st.session_state.pop('voice_error', None)
                    except RuntimeError as exc:
                        st.session_state.voice_error = str(exc)
                if st.session_state.get('voice_error'):
                    st.error(st.session_state.voice_error)
                    if st.button('Try transcription again'):
                        st.session_state.pop('processed_audio', None)
                        st.rerun()
                else:
                    st.caption('Your recording is transcribed locally on your home server. You can edit it before sending.')

        with st.form('command_form', border=False):
            command = st.text_area('Your command', key='command', height=120,
                                   placeholder='Alert me if a person is carrying a box', label_visibility='collapsed')
            submitted = st.form_submit_button('Create alert', type='primary', use_container_width=True,
                                              disabled=not connected)
        if submitted:
            if not command.strip():
                st.warning('Type a command or record your voice first.')
            else:
                try:
                    with st.spinner('Setting up your alert…'):
                        result = client.create_rule(command.strip())
                    st.session_state.recent_commands = ([command.strip()] + st.session_state.recent_commands)[:5]
                    message = 'Your alert is ready.'
                    if not result.get('monitoring_running', running):
                        message += ' Start monitoring to watch for matches.'
                    st.session_state.notice = ('success', message)
                    st.session_state.clear_composer = True
                    st.rerun()
                except RuntimeError as exc:
                    st.error(str(exc))
        st.caption('Alerts appear on your connected Fire TV. Visual checks recognise visible activity; they do not verify direction of travel.')

    if st.session_state.recent_commands:
        st.markdown('### Recent commands')
        for text in st.session_state.recent_commands:
            st.caption(text)

with watchlist:
    with st.container(border=True, key='watchlist_card'):
        title, count = st.columns([3, 1])
        title.subheader('Your watchlist')
        count.caption(f'{active} active')
        if not entries:
            st.markdown('Nothing on your watchlist yet.')
            st.caption('Create your first alert to get started.')
        for index, entry in enumerate(entries):
            rule = entry.get('rule', {})
            rule_id = rule.get('id', '')
            enabled = entry.get('enabled', True)
            st.markdown('**' + (entry.get('prompt') or rule_id.replace('_', ' ').capitalize()) + '**')
            st.caption('Watching' if enabled and running else 'Ready · monitoring paused' if enabled else 'Paused')
            pause, remove = st.columns(2)
            if pause.button('Pause' if enabled else 'Resume', key=f'toggle_{index}_{rule_id}', use_container_width=True):
                try:
                    client.request('PATCH', f'/api/v1/automations/rules/{rule_id}/toggle', json={'enabled': not enabled})
                    st.rerun()
                except RuntimeError as exc:
                    st.error(str(exc))
            if remove.button('Remove', key=f'remove_{index}_{rule_id}', use_container_width=True):
                try:
                    client.request('DELETE', f'/api/v1/automations/rules/{rule_id}')
                    st.rerun()
                except RuntimeError as exc:
                    st.error(str(exc))
            if index < len(entries) - 1:
                st.divider()

    with st.container(border=True, key='connection_card'):
        st.subheader('Home connection')
        tv = optional('/api/v1/fire-tv/status', {})
        tv_count = tv.get('active_fire_tv_clients', 0)
        st.caption(f'Fire TV · {tv_count} connected' if tv_count else 'Fire TV · waiting for a connection')
        st.caption('Microphone · ready' if voice.get('ready') else 'Microphone · unavailable')
        if st.button('Refresh status', use_container_width=True):
            st.rerun()
        if connected:
            if st.button('Pause monitoring' if running else 'Start monitoring', use_container_width=True):
                try:
                    client.request('POST', '/api/v1/automations/loop/' + ('stop' if running else 'start'))
                    st.rerun()
                except RuntimeError as exc:
                    st.error(str(exc))

with st.expander('Recent activity'):
    history = optional('/api/v1/automations/history', {}).get('events', [])
    alerts = [event for event in history if event.get('event_type') in ('visual_rule_alert', 'rule_alert')][-8:]
    if not alerts:
        st.caption('No alerts recorded yet.')
    for event in reversed(alerts):
        details = event.get('details', {})
        st.write(details.get('alert', 'Home activity detected'))
        st.caption(event.get('time_str', ''))

st.caption('Smart Ring · A quieter way to keep an eye on home.')
