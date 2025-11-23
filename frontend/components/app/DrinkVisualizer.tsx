'use client';

import { motion } from 'motion/react';

interface DrinkVisualizerProps {
  order: any;
}

export function DrinkVisualizer({ order }: DrinkVisualizerProps) {
  const { drinkType, size, milk, extras } = order;

  return (
    <motion.div
      className="flex flex-col items-center p-4 bg-white/10 backdrop-blur-md rounded-2xl border border-white/20 shadow-xl w-64"
      initial={{ opacity: 0, scale: 0.8 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.5 }}
    >
      {/* Cup Icon */}
      <motion.div
        animate={{ rotate: drinkType ? 0 : -10 }}
        transition={{ type: 'spring', stiffness: 120 }}
      >
        <svg width="96" height="96" viewBox="0 0 24 24" fill="none">
          <path
            d="M6 3h12l-1.5 16.5a3 3 0 01-3 2.5H10.5a3 3 0 01-3-2.5L6 3z"
            stroke="white"
            strokeWidth="1.5"
          />
          <path
            d="M8 3V2a1 1 0 011-1h6a1 1 0 011 1v1"
            stroke="white"
            strokeWidth="1.5"
          />
        </svg>
      </motion.div>

      {/* Drink Info */}
      <motion.div
        className="text-white mt-4 text-center space-y-1"
        animate={{ opacity: drinkType ? 1 : 0.5 }}
      >
        <p className="font-semibold text-lg">{drinkType ?? "Drink not chosen yet"}</p>
        <p className="text-sm opacity-80">{size ?? "Size?"}</p>
        <p className="text-sm opacity-80">{milk ?? "Milk?"}</p>
        {extras?.length > 0 && (
          <p className="text-xs opacity-60">Extras: {extras.join(', ')}</p>
        )}
      </motion.div>
    </motion.div>
  );
}
