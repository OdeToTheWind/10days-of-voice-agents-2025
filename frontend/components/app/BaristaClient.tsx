'use client';

import React, { useEffect, useRef, useState } from 'react';
import { Room, createLocalAudioTrack } from 'livekit-client';

/**
 * NOTE:
 * - Replace LIVEKIT_WS_URL and token endpoint with your actual values.
 * - This code expects your backend to expose an endpoint `/api/get-livekit-token`
 *   that returns a valid token string. Replace as needed.
 */

/* -------------------------
   Types
   ------------------------- */
type OrderState = {
  drinkType?: string | null;
  size?: 'small' | 'medium' | 'large' | string | null;
  milk?: string | null;
  extras?: string[] | null;
  name?: string | null;
  timestamp?: string | null;
  status?: string | null;
};

/* -------------------------
   Helper components
   ------------------------- */

function DrinkVisualizer({ order }: { order: OrderState | null }) {
  const sizeMap: Record<string, number> = { small: 48, medium: 76, large: 110 };

  const cupHeight = order?.size ? sizeMap[order.size as string] ?? 76 : 76;
  const hasWhipped = !!order?.extras?.some((e) => e?.toLowerCase?.().includes('whipped'));

  return (
    <div style={{ width: 280, padding: 16 }}>
      <div
        style={{
          background: 'rgba(255,255,255,0.06)',
          borderRadius: 16,
          padding: 16,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          border: '1px solid rgba(255,255,255,0.06)',
        }}
      >
        {/* Cup */}
        <div style={{ height: cupHeight, width: 80, position: 'relative', marginBottom: 12 }}>
          {/* cup body */}
          <div
            style={{
              position: 'absolute',
              left: 0,
              right: 0,
              height: '100%',
              margin: '0 auto',
              background: '#c67b4a',
              borderRadius: '12px 12px 10px 10px',
              boxShadow: 'inset 0 -10px 20px rgba(0,0,0,0.25)',
            }}
          />
          {/* whipped cream */}
          {hasWhipped && (
            <div
              style={{
                position: 'absolute',
                top: -20,
                left: -6,
                right: -6,
                height: 28,
                background: 'white',
                borderRadius: '18px',
                boxShadow: '0 4px 8px rgba(0,0,0,0.15)',
              }}
            />
          )}
          {/* small handle */}
          <div
            style={{
              position: 'absolute',
              right: -18,
              top: cupHeight * 0.3,
              width: 22,
              height: 44,
              border: '4px solid rgba(255,255,255,0.08)',
              borderRadius: 20,
              boxSizing: 'border-box',
            }}
          />
        </div>

        {/* Text summary */}
        <div style={{ color: 'white', textAlign: 'center' }}>
          <div style={{ fontWeight: 700 }}>{order?.drinkType ?? 'No drink yet'}</div>
          <div style={{ fontSize: 13, opacity: 0.9 }}>
            {order?.size ?? 'size ?'} • {order?.milk ?? 'milk ?'}
          </div>
          <div style={{ fontSize: 12, opacity: 0.8, marginTop: 8 }}>
            {order?.extras?.length ? `Extras: ${order.extras.join(', ')}` : 'No extras'}
          </div>
          <div style={{ fontSize: 12, opacity: 0.8, marginTop: 8 }}>
            {order?.name ? `For ${order.name}` : ''}
          </div>
        </div>
      </div>
    </div>
  );
}

/* -------------------------
   Main component
   ------------------------- */
export default function BaristaClient() {
  const [room, setRoom] = useState<Room | null>(null);
  const [connected, setConnected] = useState(false);
  const [messages, setMessages] = useState<string[]>([]);
  const [order, setOrder] = useState<OrderState | null>(null);
  const [aiSpeaking, setAiSpeaking] = useState(false);
  const [userSpeaking, setUserSpeaking] = useState(false);
  const talkTimeoutRef = useRef<number | null>(null);
  const aiTimeoutRef = useRef<number | null>(null);

  const LIVEKIT_WS_URL = process.env.NEXT_PUBLIC_LIVEKIT_WS ?? 'ws://localhost:7880';

  /* Append chat message (keeps last 200) */
  const appendMessage = (text: string) => {
    setMessages((m) => {
      const next = [...m, text];
      if (next.length > 200) next.shift();
      return next;
    });
  };

  /* Dispatch window events used by layout.tsx to change background */
  const fireAiStart = () => {
    window.dispatchEvent(new Event('ai-speaking-start'));
    setAiSpeaking(true);
    if (aiTimeoutRef.current) window.clearTimeout(aiTimeoutRef.current);
    aiTimeoutRef.current = window.setTimeout(() => {
      window.dispatchEvent(new Event('ai-speaking-end'));
      setAiSpeaking(false);
    }, 1800);
  };

  const fireUserStart = () => {
    window.dispatchEvent(new Event('user-speaking-start'));
    setUserSpeaking(true);
    if (talkTimeoutRef.current) window.clearTimeout(talkTimeoutRef.current);
  };

  const fireUserEnd = () => {
    if (talkTimeoutRef.current) window.clearTimeout(talkTimeoutRef.current);
    // small fade out so UI feels nice
    talkTimeoutRef.current = window.setTimeout(() => {
      window.dispatchEvent(new Event('user-speaking-end'));
      setUserSpeaking(false);
    }, 400);
  };

  /* Connect to LiveKit room and publish mic */
  const connectRoom = async () => {
    try {
      const tokenResp = await fetch('/api/get-livekit-token');
      if (!tokenResp.ok) {
        appendMessage('Error: could not fetch LiveKit token');
        return;
      }
      const token = await tokenResp.text();

      const r = new Room();
      await r.connect(LIVEKIT_WS_URL, token);
      setRoom(r);
      setConnected(true);
      appendMessage('Connected to barista room');

      // publish local mic
      const localAudioTrack = await createLocalAudioTrack();
      await r.localParticipant.publishTrack(localAudioTrack);

      // receive data messages (text) via LiveKit data channel
      // payload is a Uint8Array
      r.on('dataReceived', (payload: ArrayBuffer | Uint8Array, participant: any) => {
        // decode payload
        try {
          const text = new TextDecoder().decode(payload as Uint8Array);
          const sender = participant?.identity ?? 'agent';
          appendMessage(`${sender}: ${text}`);

          // fire AI speaking UI event
          fireAiStart();
        } catch (e) {
          console.warn('failed to decode data payload', e);
        }
      });

      // received remote audio track - play automatically
      r.on('trackSubscribed', (track: any, publication: any, participant: any) => {
        if (track.kind === 'audio') {
          const el = document.createElement('audio');
          el.autoplay = true;
          // some track objects expose mediaStreamTrack
          const media = track.mediaStreamTrack ? new MediaStream([track.mediaStreamTrack]) : undefined;
          if (media) el.srcObject = media;
          else if (track.attach) {
            // older helper
            const node = track.attach();
            node.autoplay = true;
            document.body.appendChild(node);
          }
          document.body.appendChild(el);
        }
      });

      // participant connected
      r.on('participantConnected', (p: any) => {
        appendMessage(`Participant joined: ${p.identity}`);
      });

      // optional: participant disconnected
      r.on('participantDisconnected', (p: any) => {
        appendMessage(`Participant left: ${p.identity}`);
      });
    } catch (err) {
      console.error(err);
      appendMessage('Connection error: see console');
    }
  };

  /* Poll for order JSON (backend writes this file) */
  useEffect(() => {
    let lastJSON: string | null = null;
    const interval = setInterval(async () => {
      try {
        const res = await fetch('/orders/order_summary.json', { cache: 'no-store' });
        if (!res.ok) return;
        const data = await res.json();
        const serialized = JSON.stringify(data);
        if (serialized !== lastJSON) {
          lastJSON = serialized;
          setOrder(data);
          appendMessage(`Order updated: ${data.size ?? ''} ${data.drinkType ?? ''} for ${data.name ?? ''}`);
          // fire AI speaking to emphasize update
          fireAiStart();

          // also emit event that session-view.tsx (or others) can listen to:
          window.dispatchEvent(
            new CustomEvent('order-update', {
              detail: data,
            })
          );
        }
      } catch (e) {
        // no-op
      }
    }, 1000);

    return () => clearInterval(interval);
  }, []);

  /* cleanup room on unmount */
  useEffect(() => {
    return () => {
      if (room && room.state !== 'disconnected') {
        try {
          room.disconnect();
        } catch {}
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* Hold-to-talk handlers */
  const handleTalkStart = () => {
    fireUserStart();
    // if you want to mute TTS while user speaks, you can send a signal to backend here via REST
  };

  const handleTalkEnd = () => {
    fireUserEnd();
  };

  return (
    <div style={{ padding: 20, color: 'white' }}>
      <div style={{ display: 'flex', gap: 20, alignItems: 'flex-start', justifyContent: 'center' }}>
        {/* Conversation / Left side */}
        <div style={{ width: 520, maxHeight: '70vh', overflowY: 'auto', borderRadius: 12, padding: 12, background: 'rgba(0,0,0,0.18)' }}>
          <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center' }}>
            <button
              onClick={connectRoom}
              style={{
                padding: '10px 14px',
                background: '#6b4f4f',
                borderRadius: 8,
                color: 'white',
                fontWeight: 700,
                border: 'none',
                cursor: 'pointer',
              }}
            >
              {connected ? 'Connected' : 'Connect & Talk'}
            </button>

            <div style={{ display: 'flex', gap: 8 }}>
              <div style={{ fontSize: 13, opacity: 0.9 }}>{connected ? 'Live' : 'Offline'}</div>
              <div style={{ fontSize: 13, opacity: 0.8 }}>
                AI: <strong style={{ color: aiSpeaking ? '#7ef3d7' : '#9aa' }}>{aiSpeaking ? 'speaking' : 'idle'}</strong>
              </div>
              <div style={{ fontSize: 13, opacity: 0.8 }}>
                You: <strong style={{ color: userSpeaking ? '#9ad1ff' : '#9aa' }}>{userSpeaking ? 'speaking' : 'idle'}</strong>
              </div>
            </div>
          </div>

          <div style={{ marginBottom: 8 }}>
            <div style={{ marginBottom: 8, fontWeight: 700 }}>Conversation</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {messages.length === 0 && <div style={{ color: '#cfcfcf' }}>No messages yet.</div>}
              {messages.map((m, i) => (
                <div key={i} style={{ padding: 8, borderRadius: 8, background: 'rgba(255,255,255,0.04)' }}>
                  <div style={{ fontSize: 13 }}>{m}</div>
                </div>
              ))}
            </div>
          </div>

          {/* hold-to-talk */}
          <div style={{ marginTop: 12 }}>
            <div style={{ fontSize: 13, marginBottom: 6 }}>Hold to talk</div>
            <button
              onPointerDown={handleTalkStart}
              onPointerUp={handleTalkEnd}
              onPointerLeave={handleTalkEnd}
              style={{
                width: 160,
                height: 44,
                borderRadius: 10,
                background: userSpeaking ? '#0b66ff' : '#2b3a67',
                color: 'white',
                border: 'none',
                fontWeight: 700,
                cursor: 'pointer',
              }}
            >
              {userSpeaking ? 'Listening...' : 'Hold to Talk'}
            </button>
          </div>
        </div>

        {/* Visualizer / Right side */}
        <div>
          <DrinkVisualizer order={order} />
        </div>
      </div>
    </div>
  );
}
