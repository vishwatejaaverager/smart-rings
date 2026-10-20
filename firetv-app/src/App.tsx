import React, {useEffect, useState} from 'react';
import {
  Image,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';

export const MAX_EVENT_HISTORY = 50;

export const DEFAULT_WS_URL = 'ws://192.168.31.125:8000/ws/alerts';

export interface LiveAlertEvent {
  rule: string;
  action: string;
  message: string;
  event_type: string;
  device_id: string;
  timestamp?: number | string | null;
  origin?: string | null;
  snapshot?: {
    format?: string;
    image_base64?: string;
  } | null;
}

export type ConnectionStatus = 'Connecting' | 'Connected' | 'Disconnected';

export interface AppProps {
  wsUrl?: string;
  initialEvents?: LiveAlertEvent[];
}

export const formatEventTime = (timestamp?: number | string | null): string => {
  if (!timestamp) {
    return new Date().toLocaleTimeString([], {
      hour: 'numeric',
      minute: '2-digit',
      second: '2-digit',
    });
  }

  const num = typeof timestamp === 'number' ? timestamp : Number(timestamp);
  const date =
    !isNaN(num) && num > 0
      ? new Date(num < 1e11 ? num * 1000 : num)
      : new Date(String(timestamp));

  if (isNaN(date.getTime())) {
    return String(timestamp);
  }

  return date.toLocaleTimeString([], {
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit',
  });
};

export const App: React.FC<AppProps> = ({
  wsUrl = DEFAULT_WS_URL,
  initialEvents = [],
}) => {
  const [connectionStatus, setConnectionStatus] =
    useState<ConnectionStatus>('Connecting');
  const [events, setEvents] = useState<LiveAlertEvent[]>(
    initialEvents.slice(0, MAX_EVENT_HISTORY),
  );
  const [activeAlert, setActiveAlert] = useState<LiveAlertEvent | null>(null);
  const [focusedAction, setFocusedAction] = useState<string | null>(null);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let isMounted = true;

    const connect = () => {
      if (!isMounted) {
        return;
      }
      setConnectionStatus('Connecting');

      try {
        if (typeof WebSocket === 'undefined') {
          setConnectionStatus('Disconnected');
          return;
        }

        ws = new WebSocket(wsUrl);

        ws.onopen = () => {
          if (!isMounted) {
            return;
          }
          setConnectionStatus('Connected');
        };

        ws.onmessage = (event) => {
          if (!isMounted) {
            return;
          }
          try {
            const rawData =
              typeof event.data === 'string'
                ? JSON.parse(event.data)
                : event.data;

            if (
              rawData &&
              typeof rawData === 'object' &&
              (typeof rawData.message === 'string' ||
                typeof rawData.rule === 'string')
            ) {
              const liveEvent: LiveAlertEvent = {
                rule:
                  typeof rawData.rule === 'string'
                    ? rawData.rule
                    : 'Unnamed rule',
                action:
                  typeof rawData.action === 'string'
                    ? rawData.action
                    : 'unknown',
                message:
                  typeof rawData.message === 'string' ? rawData.message : '',
                event_type:
                  typeof rawData.event_type === 'string'
                    ? rawData.event_type
                    : 'unknown',
                device_id:
                  typeof rawData.device_id === 'string'
                    ? rawData.device_id
                    : 'unknown',
                timestamp: rawData.timestamp ?? null,
                origin:
                  typeof rawData.origin === 'string' ? rawData.origin : null,
                snapshot:
                  typeof rawData.snapshot?.image_base64 === 'string'
                    ? rawData.snapshot
                    : null,
              };

              setEvents((prev) =>
                [liveEvent, ...prev].slice(0, MAX_EVENT_HISTORY),
              );

              if (liveEvent.action === 'show_alert_on_fire_tv') {
                setActiveAlert(liveEvent);
              }
            }
          } catch {
            // Safely ignore malformed WebSocket messages
          }
        };

        ws.onerror = () => {
          if (!isMounted) {
            return;
          }
        };

        ws.onclose = () => {
          if (!isMounted) {
            return;
          }
          setConnectionStatus('Disconnected');
          reconnectTimer = setTimeout(() => {
            if (isMounted) {
              connect();
            }
          }, 3000);
        };
      } catch {
        if (!isMounted) {
          return;
        }
        setConnectionStatus('Disconnected');
        reconnectTimer = setTimeout(() => {
          if (isMounted) {
            connect();
          }
        }, 3000);
      }
    };

    connect();

    return () => {
      isMounted = false;
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
      }
      if (ws) {
        ws.onopen = null;
        ws.onmessage = null;
        ws.onerror = null;
        ws.onclose = null;
        ws.close();
      }
    };
  }, [wsUrl]);

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <View style={styles.brand}>
          <View style={styles.brandMark}>
            <View style={styles.brandMarkInner} />
          </View>
          <Text style={styles.headerTitle}>Smart Ring</Text>
          <Text style={styles.headerSubtitle}>Home alerts</Text>
        </View>
        <View style={styles.statusBadge} testID="connection-status-badge">
          <View
            style={[
              styles.statusDot,
              connectionStatus === 'Connected' && styles.connectedDot,
            ]}
          />
          <Text style={styles.statusText} testID="connection-status">
            {connectionStatus}
          </Text>
        </View>
      </View>

      <View style={styles.content}>
        <View style={[styles.hero, activeAlert ? styles.activeHero : null]}>
          {activeAlert ? (
            <View testID="fire-tv-alert-banner" style={styles.heroContent}>
              <View style={styles.heroTopline}>
                <Text style={styles.eyebrow}>FIRE TV ALERT</Text>
                <Text style={styles.time}>
                  {formatEventTime(activeAlert.timestamp)}
                </Text>
              </View>
              {activeAlert.origin && activeAlert.origin !== 'live_webhook' ? (
                <Text style={styles.origin} testID="alert-banner-origin-badge">
                  {activeAlert.origin.replace(/_/g, ' ').toUpperCase()}
                </Text>
              ) : null}
              <Text style={styles.alertTitle}>
                {activeAlert.message || activeAlert.rule}
              </Text>
              <Text style={styles.heroDescription}>
                An alert from your home. Review it when you’re ready.
              </Text>
              {activeAlert.snapshot?.image_base64 ? (
                <View
                  style={styles.snapshotContainer}
                  testID="alert-snapshot-container">
                  <Image
                    source={{
                      uri: `data:image/jpeg;base64,${activeAlert.snapshot.image_base64}`,
                    }}
                    style={styles.snapshot}
                    resizeMode="contain"
                    testID="alert-snapshot-image"
                  />
                </View>
              ) : null}
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Dismiss current alert"
                testID="dismiss-alert-banner"
                onPress={() => setActiveAlert(null)}
                onFocus={() => setFocusedAction('dismiss')}
                onBlur={() => setFocusedAction(null)}
                style={[
                  styles.dismissButton,
                  focusedAction === 'dismiss' && styles.focusedButton,
                ]}>
                <Text style={styles.dismissText}>Dismiss alert</Text>
              </Pressable>
            </View>
          ) : (
            <View style={styles.heroContent}>
              <View style={styles.quietIcon}>
                <View style={styles.quietIconInner} />
              </View>
              <Text style={styles.eyebrow}>YOUR HOME, AT A GLANCE</Text>
              <Text style={styles.quietTitle}>All quiet for now.</Text>
              <Text style={styles.heroDescription}>
                {connectionStatus === 'Connected'
                  ? 'New alerts from your home will appear here.'
                  : 'Waiting to connect to your home server.'}
              </Text>
            </View>
          )}
        </View>

        <View style={styles.history}>
          <View style={styles.historyHeader}>
            <Text style={styles.sectionTitle}>Recent activity</Text>
            {events.length > 0 ? (
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Clear recent activity"
                testID="clear-messages-button"
                onFocus={() => setFocusedAction('clear')}
                onBlur={() => setFocusedAction(null)}
                onPress={() => {
                  setEvents([]);
                  setActiveAlert(null);
                }}
                style={[
                  styles.clearButton,
                  focusedAction === 'clear' && styles.focusedButton,
                ]}>
                <Text style={styles.clearText}>Clear</Text>
              </Pressable>
            ) : null}
          </View>
          <ScrollView
            style={styles.eventsList}
            showsVerticalScrollIndicator={false}>
            {events.length === 0 ? (
              <View style={styles.emptyHistory} testID="events-empty-state">
                <Text style={styles.emptyTitle}>No events yet</Text>
                <Text style={styles.emptyDescription}>
                  Your latest home activity will be saved here.
                </Text>
              </View>
            ) : (
              events.map((event, index) => (
                <View
                  key={`${event.timestamp}-${index}`}
                  style={styles.eventRow}
                  testID={`event-card-${index}`}>
                  <View style={styles.eventMeta}>
                    <Text style={styles.eventTime}>
                      {formatEventTime(event.timestamp)}
                    </Text>
                    {event.origin && event.origin !== 'live_webhook' ? (
                      <Text
                        style={styles.origin}
                        testID={`origin-badge-${index}`}>
                        {event.origin.replace(/_/g, ' ').toUpperCase()}
                      </Text>
                    ) : null}
                  </View>
                  <Text style={styles.eventMessage}>
                    {event.message || event.rule}
                  </Text>
                </View>
              ))
            )}
          </ScrollView>
        </View>
      </View>
      <Text style={styles.footer}>A little more peace of mind.</Text>
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#101A23',
    paddingHorizontal: 52,
    paddingTop: 32,
    paddingBottom: 20,
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 32,
  },
  brand: {flexDirection: 'row', alignItems: 'center', gap: 12},
  brandMark: {
    width: 30,
    height: 30,
    borderRadius: 15,
    borderWidth: 2,
    borderColor: '#A7C6B7',
    alignItems: 'center',
    justifyContent: 'center',
  },
  brandMarkInner: {
    width: 10,
    height: 10,
    borderRadius: 5,
    backgroundColor: '#A7C6B7',
  },
  headerTitle: {
    fontSize: 25,
    fontWeight: '600',
    color: '#F4F3EC',
    letterSpacing: -0.6,
  },
  headerSubtitle: {fontSize: 16, color: '#82919B', marginLeft: 10},
  statusBadge: {flexDirection: 'row', alignItems: 'center', gap: 9},
  statusDot: {width: 7, height: 7, borderRadius: 4, backgroundColor: '#B89E70'},
  connectedDot: {backgroundColor: '#9DBDA9'},
  statusText: {fontSize: 15, color: '#B2BFC4'},
  content: {flex: 1, flexDirection: 'row', gap: 32},
  hero: {
    flex: 1.7,
    borderRadius: 22,
    backgroundColor: '#182731',
    padding: 32,
    justifyContent: 'center',
  },
  activeHero: {
    backgroundColor: '#22332F',
    borderWidth: 1,
    borderColor: '#4D7061',
  },
  heroContent: {gap: 16},
  heroTopline: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  eyebrow: {
    fontSize: 12,
    letterSpacing: 2,
    fontWeight: '600',
    color: '#ACC7B7',
  },
  time: {fontSize: 15, color: '#A5B5AF'},
  quietTitle: {
    fontSize: 43,
    lineHeight: 49,
    fontWeight: '500',
    letterSpacing: -1.4,
    color: '#F2F2EA',
  },
  alertTitle: {
    fontSize: 30,
    lineHeight: 39,
    fontWeight: '500',
    letterSpacing: -0.7,
    color: '#F2F2EA',
  },
  heroDescription: {
    fontSize: 18,
    lineHeight: 27,
    color: '#A7B8BA',
    maxWidth: 460,
  },
  quietIcon: {
    width: 64,
    height: 64,
    borderRadius: 32,
    borderWidth: 1,
    borderColor: '#3C5457',
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 12,
  },
  quietIconInner: {
    width: 22,
    height: 22,
    borderRadius: 11,
    backgroundColor: '#8EAF9E',
  },
  dismissButton: {
    alignSelf: 'flex-start',
    paddingHorizontal: 22,
    paddingVertical: 12,
    borderRadius: 9,
    backgroundColor: '#D5E2D6',
    borderWidth: 2,
    borderColor: 'transparent',
    marginTop: 6,
  },
  dismissText: {fontSize: 16, fontWeight: '600', color: '#19342A'},
  focusedButton: {borderColor: '#FFFFFF', transform: [{scale: 1.04}]},
  snapshotContainer: {
    height: 155,
    borderRadius: 12,
    overflow: 'hidden',
    backgroundColor: '#14231F',
  },
  snapshot: {height: '100%', width: '100%'},
  history: {flex: 1, paddingTop: 8},
  historyHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 16,
  },
  sectionTitle: {fontSize: 19, fontWeight: '500', color: '#E3E8E5'},
  clearButton: {
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 8,
    borderWidth: 2,
    borderColor: 'transparent',
  },
  clearText: {fontSize: 14, color: '#A5B6BA'},
  eventsList: {flex: 1},
  emptyHistory: {paddingTop: 18},
  emptyTitle: {fontSize: 18, color: '#A8B7BC', marginBottom: 9},
  emptyDescription: {fontSize: 15, lineHeight: 23, color: '#71848E'},
  eventRow: {
    paddingVertical: 17,
    borderBottomWidth: 1,
    borderBottomColor: '#253640',
  },
  eventMeta: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginBottom: 8,
    alignItems: 'center',
  },
  eventTime: {fontSize: 13, color: '#82979E'},
  origin: {fontSize: 10, letterSpacing: 1, color: '#B5AE91'},
  eventMessage: {fontSize: 17, lineHeight: 25, color: '#D9E2DE'},
  footer: {fontSize: 12, color: '#617881', marginTop: 20},
});
