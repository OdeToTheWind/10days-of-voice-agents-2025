'use client';

import React from 'react';
import { motion } from 'motion/react';
import { cn } from '@/lib/utils';
import type { OrderState } from '@/hooks/useOrderState';

interface CoffeeAnimationProps {
  order: OrderState;
  className?: string;
}

export function CoffeeAnimation({ order, className }: CoffeeAnimationProps) {
  const getCupSize = () => {
    switch (order.size?.toLowerCase()) {
      case 'small': return 'h-16 w-12';
      case 'large': return 'h-24 w-16';
      default: return 'h-20 w-14'; // medium
    }
  };

  const getDrinkColor = () => {
    const drink = order.drinkType?.toLowerCase();
    if (drink?.includes('latte') || drink?.includes('cappuccino')) return 'bg-amber-700';
    if (drink?.includes('americano') || drink?.includes('espresso')) return 'bg-amber-900';
    if (drink?.includes('mocha')) return 'bg-amber-800';
    if (drink?.includes('frappuccino')) return 'bg-blue-200';
    return 'bg-amber-700'; // default coffee color
  };

  const getMilkColor = () => {
    const milk = order.milk?.toLowerCase();
    if (milk?.includes('oat')) return 'bg-amber-100';
    if (milk?.includes('almond')) return 'bg-stone-100';
    if (milk?.includes('soy')) return 'bg-yellow-100';
    if (milk?.includes('coconut')) return 'bg-white';
    return 'bg-stone-50'; // regular milk
  };

  const hasWhippedCream = order.extras?.some(extra => 
    extra.toLowerCase().includes('whipped') || extra.toLowerCase().includes('cream')
  );

  const isIced = order.extras?.some(extra => 
    extra.toLowerCase().includes('iced') || extra.toLowerCase().includes('cold')
  );

  return (
    <div className={cn('flex flex-col items-center justify-center p-8', className)}>
      <motion.div
        initial={{ scale: 0, rotate: -180 }}
        animate={{ scale: 1, rotate: 0 }}
        transition={{ 
          type: "spring", 
          stiffness: 260, 
          damping: 20,
          delay: 0.2 
        }}
        className="relative"
      >
        {/* Cup */}
        <div className={cn(
          'relative border-4 border-stone-300 rounded-b-lg',
          getCupSize(),
          'bg-white shadow-lg'
        )}>
          {/* Coffee/Drink */}
          <motion.div
            initial={{ height: 0 }}
            animate={{ height: '85%' }}
            transition={{ duration: 1, delay: 0.5 }}
            className={cn(
              'absolute bottom-0 left-0 right-0 rounded-b-md',
              getDrinkColor()
            )}
          />
          
          {/* Milk foam layer */}
          {order.milk && order.milk !== 'no milk' && (
            <motion.div
              initial={{ height: 0 }}
              animate={{ height: '20%' }}
              transition={{ duration: 0.8, delay: 1 }}
              className={cn(
                'absolute top-2 left-0 right-0 rounded-t-md opacity-80',
                getMilkColor()
              )}
            />
          )}

          {/* Whipped cream */}
          {hasWhippedCream && (
            <motion.div
              initial={{ scale: 0 }}
              animate={{ scale: 1 }}
              transition={{ duration: 0.5, delay: 1.5 }}
              className="absolute -top-2 left-1/2 transform -translate-x-1/2 w-8 h-4 bg-white rounded-full shadow-sm"
            />
          )}

          {/* Ice cubes for iced drinks */}
          {isIced && (
            <>
              <motion.div
                initial={{ opacity: 0, y: -10 }}
                animate={{ opacity: 0.7, y: 0 }}
                transition={{ duration: 0.3, delay: 1.2 }}
                className="absolute top-1/4 left-1/4 w-2 h-2 bg-blue-100 rounded-sm"
              />
              <motion.div
                initial={{ opacity: 0, y: -10 }}
                animate={{ opacity: 0.7, y: 0 }}
                transition={{ duration: 0.3, delay: 1.4 }}
                className="absolute top-1/3 right-1/4 w-2 h-2 bg-blue-100 rounded-sm"
              />
            </>
          )}
        </div>

        {/* Cup handle */}
        <div className="absolute right-0 top-1/4 w-3 h-6 border-2 border-stone-300 rounded-r-full bg-transparent" />

        {/* Steam animation for hot drinks */}
        {!isIced && (
          <div className="absolute -top-8 left-1/2 transform -translate-x-1/2">
            {[...Array(3)].map((_, i) => (
              <motion.div
                key={i}
                initial={{ opacity: 0, y: 0, scale: 0.5 }}
                animate={{ 
                  opacity: [0, 0.6, 0],
                  y: [-10, -20, -30],
                  scale: [0.5, 1, 1.5]
                }}
                transition={{
                  duration: 2,
                  repeat: Infinity,
                  delay: i * 0.3 + 2,
                  ease: "easeOut"
                }}
                className={cn(
                  'absolute w-1 h-1 bg-gray-300 rounded-full',
                  i === 0 && 'left-0',
                  i === 1 && 'left-2',
                  i === 2 && 'left-4'
                )}
              />
            ))}
          </div>
        )}
      </motion.div>

      {/* Order details */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 2.5 }}
        className="mt-6 text-center"
      >
        <h3 className="text-lg font-semibold text-green-700 mb-2">
          Order Ready! ✨
        </h3>
        <div className="text-sm text-muted-foreground space-y-1">
          <p className="font-medium capitalize">
            {order.size} {order.drinkType}
          </p>
          {order.milk && order.milk !== 'no milk' && (
            <p>with {order.milk}</p>
          )}
          {order.extras && order.extras.length > 0 && (
            <p>{order.extras.join(', ')}</p>
          )}
          <p className="font-medium text-green-600 mt-2">
            For {order.name}
          </p>
        </div>
      </motion.div>
    </div>
  );
}