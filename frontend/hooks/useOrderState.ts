'use client';

import { useEffect, useState } from 'react';
import { useRoomContext } from '@livekit/components-react';
import { DataPacket_Kind, RemoteParticipant } from 'livekit-client';

export interface OrderState {
  drinkType?: string;
  size?: string;
  milk?: string;
  extras?: string[];
  name?: string;
}

export interface OrderUpdate {
  type: 'order_update' | 'order_complete';
  data: OrderState;
}

export function useOrderState() {
  const [orderState, setOrderState] = useState<OrderState>({});
  const [isOrderComplete, setIsOrderComplete] = useState(false);
  const [completedOrder, setCompletedOrder] = useState<OrderState | null>(null);
  const room = useRoomContext();

  useEffect(() => {
    if (!room) return;

    const handleDataReceived = (
      payload: Uint8Array,
      participant?: RemoteParticipant,
      kind?: DataPacket_Kind,
      topic?: string
    ) => {
      if (topic !== 'coffee_order') return;

      try {
        const decoder = new TextDecoder();
        const message = decoder.decode(payload);
        const update: OrderUpdate = JSON.parse(message);

        if (update.type === 'order_update') {
          setOrderState(update.data);
          setIsOrderComplete(false);
        } else if (update.type === 'order_complete') {
          setCompletedOrder(update.data);
          setIsOrderComplete(true);
          // Reset order state after showing completion
          setTimeout(() => {
            setOrderState({});
            setIsOrderComplete(false);
            setCompletedOrder(null);
          }, 5000); // Show completion for 5 seconds
        }
      } catch (error) {
        console.error('Failed to parse order update:', error);
      }
    };

    room.on('dataReceived', handleDataReceived);

    return () => {
      room.off('dataReceived', handleDataReceived);
    };
  }, [room]);

  return {
    orderState,
    isOrderComplete,
    completedOrder,
  };
}