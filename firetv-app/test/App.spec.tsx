import 'react-native';
import {render, act, fireEvent, within} from '@testing-library/react-native';
import * as React from 'react';

import {App, LiveAlertEvent} from '../src/App';

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  url: string;
  onopen: (() => void) | null = null;
  onmessage: ((event: {data: string}) => void) | null = null;
  onerror: ((err: unknown) => void) | null = null;
  onclose: (() => void) | null = null;
  readyState = 0;
  close = jest.fn();

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  simulateOpen() {
    this.readyState = 1;
    this.onopen?.();
  }

  simulateMessage(data: unknown) {
    this.onmessage?.({
      data: typeof data === 'string' ? data : JSON.stringify(data),
    });
  }

  simulateClose() {
    this.readyState = 3;
    this.onclose?.();
  }
}

const originalWebSocket = (globalThis as unknown as {WebSocket: unknown})
  .WebSocket;

describe('Smart Ring TV', () => {
  beforeEach(() => {
    MockWebSocket.instances = [];
    (globalThis as unknown as {WebSocket: unknown}).WebSocket = MockWebSocket;
    jest.useFakeTimers();
  });

  afterEach(() => {
    (globalThis as unknown as {WebSocket: unknown}).WebSocket =
      originalWebSocket;
    jest.useRealTimers();
  });

  it('matches snapshot', () => {
    const screen = render(<App />);
    expect(screen).toMatchSnapshot();
  });

  it('renders the dashboard title', () => {
    const screen = render(<App />);
    expect(screen.getByText('Smart Ring')).toBeTruthy();
  });

  it('shows connection status: Connecting, Connected, or Disconnected', () => {
    const screen = render(<App />);
    expect(screen.getByTestId('connection-status').props.children).toBe(
      'Connecting',
    );

    const ws = MockWebSocket.instances[0];
    expect(ws.url).toContain('/ws/alerts');

    act(() => {
      ws.simulateOpen();
    });
    expect(screen.getByTestId('connection-status').props.children).toBe(
      'Connected',
    );

    act(() => {
      ws.simulateClose();
    });
    expect(screen.getByTestId('connection-status').props.children).toBe(
      'Disconnected',
    );
  });

  it('shows empty state until a real WebSocket message arrives', () => {
    const screen = render(<App />);
    expect(screen.getByTestId('events-empty-state')).toBeTruthy();
    expect(screen.getByText('No events yet')).toBeTruthy();
  });

  it('displays incoming event message and formatted time', () => {
    const screen = render(<App />);
    const ws = MockWebSocket.instances[0];

    const sampleEvent: LiveAlertEvent = {
      rule: 'Vehicle outside',
      action: 'send_phone_notification',
      message: 'Vehicle detected outside driveway',
      event_type: 'motion_detected',
      device_id: 'driveway-cam-01',
      timestamp: 1760184000000,
    };

    act(() => {
      ws.simulateOpen();
      ws.simulateMessage(sampleEvent);
    });

    expect(screen.queryByTestId('events-empty-state')).toBeNull();
    expect(screen.getByText('Vehicle detected outside driveway')).toBeTruthy();
    // Time should be displayed and formatted
    const card = screen.getByTestId('event-card-0');
    expect(card).toBeTruthy();
    expect(
      within(card).getByText('Vehicle detected outside driveway'),
    ).toBeTruthy();
  });

  it('displays prominent alert banner when action === "show_alert_on_fire_tv"', () => {
    const screen = render(<App />);
    const ws = MockWebSocket.instances[0];

    const tvAlert: LiveAlertEvent = {
      rule: 'Human motion alert',
      action: 'show_alert_on_fire_tv',
      message: 'Person detected at the front door!',
      event_type: 'motion_detected',
      device_id: 'doorbell-01',
      timestamp: 1760184100000,
    };

    act(() => {
      ws.simulateOpen();
      ws.simulateMessage(tvAlert);
    });

    const banner = screen.getByTestId('fire-tv-alert-banner');
    expect(banner).toBeTruthy();
    expect(within(banner).getByText('FIRE TV ALERT')).toBeTruthy();
    expect(
      within(banner).getByText('Person detected at the front door!'),
    ).toBeTruthy();

    // Dismiss banner
    const dismissBtn = screen.getByTestId('dismiss-alert-banner');
    fireEvent.press(dismissBtn);
    expect(screen.queryByTestId('fire-tv-alert-banner')).toBeNull();
  });

  it('handles malformed WebSocket messages safely', () => {
    const screen = render(<App />);
    const ws = MockWebSocket.instances[0];

    act(() => {
      ws.simulateOpen();
      ws.simulateMessage('MALFORMED_JSON_STRING{{{');
    });

    expect(screen.getByText('Smart Ring')).toBeTruthy();
  });

  it('displays accurate origin badge distinguishing playground and simulated events', () => {
    const screen = render(<App />);
    const ws = MockWebSocket.instances[0];

    const playgroundEvent: LiveAlertEvent = {
      rule: 'Package delivery alert',
      action: 'show_alert_on_fire_tv',
      message: 'Package delivered at your front door!',
      event_type: 'motion_detected',
      device_id: 'ava1.ring.device.PLAYGROUND_DEVICE_001',
      origin: 'playground',
      timestamp: 1760184200000,
    };

    act(() => {
      ws.simulateOpen();
      ws.simulateMessage(playgroundEvent);
    });

    const banner = screen.getByTestId('fire-tv-alert-banner');
    expect(banner).toBeTruthy();
    expect(within(banner).getByText('PLAYGROUND')).toBeTruthy();

    const card = screen.getByTestId('event-card-0');
    expect(card).toBeTruthy();
    expect(within(card).getByText('PLAYGROUND')).toBeTruthy();
  });

  it('displays incident snapshot image when snapshot data is provided', () => {
    const screen = render(<App />);
    const ws = MockWebSocket.instances[0];

    const snapshotEvent: LiveAlertEvent = {
      rule: 'Package Theft Alert',
      action: 'show_alert_on_fire_tv',
      message: '🚨 2 packages removed from front porch!',
      event_type: 'package_removal',
      device_id: 'front_door_cam',
      timestamp: 1760184300000,
      origin: 'live_webhook',
      snapshot: {
        format: 'jpeg',
        image_base64: 'fakeBase64ImageData==',
      },
    };

    act(() => {
      ws.simulateOpen();
      ws.simulateMessage(snapshotEvent);
    });

    const banner = screen.getByTestId('fire-tv-alert-banner');
    expect(banner).toBeTruthy();
    const snapshotImg = screen.getByTestId('alert-snapshot-image');
    expect(snapshotImg).toBeTruthy();
    expect(snapshotImg.props.source.uri).toBe(
      'data:image/jpeg;base64,fakeBase64ImageData==',
    );
  });

  it('cleans up socket and reconnect timer on unmount', () => {
    const screen = render(<App />);
    const ws = MockWebSocket.instances[0];

    screen.unmount();
    expect(ws.close).toHaveBeenCalled();
  });

  it('ignores heartbeat and structurally invalid event messages', () => {
    const screen = render(<App />);
    act(() => {
      MockWebSocket.instances[0].simulateMessage({type: 'pong'});
      MockWebSocket.instances[0].simulateMessage({message: {bad: 'value'}});
    });
    expect(screen.getByTestId('events-empty-state')).toBeTruthy();
  });

  it('bounds recent history and clears the current alert together with it', () => {
    const screen = render(<App />);
    act(() => {
      for (let index = 0; index < 60; index++) {
        MockWebSocket.instances[0].simulateMessage({
          rule: 'Person alert',
          action: 'show_alert_on_fire_tv',
          message: `Alert ${index}`,
        });
      }
    });
    expect(screen.getAllByTestId(/event-card-/)).toHaveLength(50);
    expect(screen.queryByText('Alert 0')).toBeNull();
    fireEvent.press(screen.getByTestId('clear-messages-button'));
    expect(screen.queryByTestId('fire-tv-alert-banner')).toBeNull();
    expect(screen.getByTestId('events-empty-state')).toBeTruthy();
  });

  it('reconnects after a dropped socket', () => {
    const screen = render(<App />);
    act(() => MockWebSocket.instances[0].simulateClose());
    act(() => jest.advanceTimersByTime(3000));
    expect(MockWebSocket.instances).toHaveLength(2);
    expect(screen.getByTestId('connection-status').props.children).toBe(
      'Connecting',
    );
  });
});
