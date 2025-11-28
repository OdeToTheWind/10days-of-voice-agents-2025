// import { Button } from '@/components/livekit/button';

// function WelcomeImage() {
//   return (
//     <svg
//       width="64"
//       height="64"
//       viewBox="0 0 64 64"
//       fill="none"
//       xmlns="http://www.w3.org/2000/svg"
//       className="text-fg0 mb-4 size-16"
//     >
//       <path
//         d="M15 24V40C15 40.7957 14.6839 41.5587 14.1213 42.1213C13.5587 42.6839 12.7956 43 12 43C11.2044 43 10.4413 42.6839 9.87868 42.1213C9.31607 41.5587 9 40.7957 9 40V24C9 23.2044 9.31607 22.4413 9.87868 21.8787C10.4413 21.3161 11.2044 21 12 21C12.7956 21 13.5587 21.3161 14.1213 21.8787C14.6839 22.4413 15 23.2044 15 24ZM22 5C21.2044 5 20.4413 5.31607 19.8787 5.87868C19.3161 6.44129 19 7.20435 19 8V56C19 56.7957 19.3161 57.5587 19.8787 58.1213C20.4413 58.6839 21.2044 59 22 59C22.7956 59 23.5587 58.6839 24.1213 58.1213C24.6839 57.5587 25 56.7957 25 56V8C25 7.20435 24.6839 6.44129 24.1213 5.87868C23.5587 5.31607 22.7956 5 22 5ZM32 13C31.2044 13 30.4413 13.3161 29.8787 13.8787C29.3161 14.4413 29 15.2044 29 16V48C29 48.7957 29.3161 49.5587 29.8787 50.1213C30.4413 50.6839 31.2044 51 32 51C32.7956 51 33.5587 50.6839 34.1213 50.1213C34.6839 49.5587 35 48.7957 35 48V16C35 15.2044 34.6839 14.4413 34.1213 13.8787C33.5587 13.3161 32.7956 13 32 13ZM42 21C41.2043 21 40.4413 21.3161 39.8787 21.8787C39.3161 22.4413 39 23.2044 39 24V40C39 40.7957 39.3161 41.5587 39.8787 42.1213C40.4413 42.6839 41.2043 43 42 43C42.7957 43 43.5587 42.6839 44.1213 42.1213C44.6839 41.5587 45 40.7957 45 40V24C45 23.2044 44.6839 22.4413 44.1213 21.8787C43.5587 21.3161 42.7957 21 42 21ZM52 17C51.2043 17 50.4413 17.3161 49.8787 17.8787C49.3161 18.4413 49 19.2044 49 20V44C49 44.7957 49.3161 45.5587 49.8787 46.1213C50.4413 46.6839 51.2043 47 52 47C52.7957 47 53.5587 46.6839 54.1213 46.1213C54.6839 45.5587 55 44.7957 55 44V20C55 19.2044 54.6839 18.4413 54.1213 17.8787C53.5587 17.3161 52.7957 17 52 17Z"
//         fill="currentColor"
//       />
//     </svg>
//   );
// }

// interface WelcomeViewProps {
//   startButtonText: string;
//   onStartCall: () => void;
// }

// export const WelcomeView = ({
//   startButtonText,
//   onStartCall,
//   ref,
// }: React.ComponentProps<'div'> & WelcomeViewProps) => {
//   return (
//     <div ref={ref}>
//       <section className="bg-background flex flex-col items-center justify-center text-center">
//         <WelcomeImage />

//         <p className="text-foreground max-w-prose pt-1 leading-6 font-medium">
//           Chat live with your voice AI agent
//         </p>

//         <Button variant="primary" size="lg" onClick={onStartCall} className="mt-6 w-64 font-mono">
//           {startButtonText}
//         </Button>
//       </section>

//       <div className="fixed bottom-5 left-0 flex w-full items-center justify-center">
//         <p className="text-muted-foreground max-w-prose pt-1 text-xs leading-5 font-normal text-pretty md:text-sm">
//           Need help getting set up? Check out the{' '}
//           <a
//             target="_blank"
//             rel="noopener noreferrer"
//             href="https://docs.livekit.io/agents/start/voice-ai/"
//             className="underline"
//           >
//             Voice AI quickstart
//           </a>
//           .
//         </p>
//       </div>
//     </div>
//   );
// };


'use client';

import { motion } from 'motion/react';
import Image from 'next/image';
import { Button } from '@/components/livekit/button';

const MotionText = motion.create('h1');
const MotionSubtext = motion.create('p');

interface WelcomeViewProps {
  startButtonText: string;
  onStartCall: () => void;
}

export const WelcomeView = ({
  startButtonText,
  onStartCall,
  ref,
}: React.ComponentProps<'div'> & WelcomeViewProps) => {

  return (
    <div ref={ref}>
      {/* Animated Wave Background */}
      <div className="fixed inset-0 z-0 overflow-hidden bg-[#611f02]">
        <div className="wave-container absolute inset-0">
          <div className="wave wave1"></div>
          <div className="wave wave2"></div>
          <div className="wave wave3"></div>
          <div className="wave wave4"></div>
        </div>
      </div>
      <section className="relative z-10 flex min-h-svh w-full flex-col items-center justify-center text-center">
        {/* Logo and Blinky text side by side */}
        <motion.div
          className="mb-2 flex items-center justify-center gap-2"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{
            duration: 0.7,
            ease: [0.16, 1, 0.3, 1],
            delay: 0.4,
          }}
        >
          {/* Logo with fade-in and scale animation */}
          <motion.div
            initial={{ opacity: 0, scale: 0.8 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{
              duration: 0.6,
              ease: [0.16, 1, 0.3, 1],
              delay: 0.2,
            }}
          >
            <Image
              src="/Blink_Basket_logo.png"   
              alt="Blinky Logo"
              width={100}
              height={100}
              priority
              className="h-auto w-auto"
            />
          </motion.div>

          {/* Large gradient text "Blinky" */}
          <MotionText className="bg-gradient-to-r from-white via-white/90 to-white/70 bg-clip-text text-6xl font-extrabold tracking-tight text-transparent md:text-7xl lg:text-8xl">
            Blinky
          </MotionText>
        </motion.div>

        {/* Large gradient text "Basket" */}
        <MotionText
          className="mb-8 bg-gradient-to-r from-white/90 via-white to-white/80 bg-clip-text text-5xl font-bold tracking-tight text-transparent md:text-6xl lg:text-7xl"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{
            duration: 0.7,
            ease: [0.16, 1, 0.3, 1],
            delay: 0.6,
          }}
        >
          Basket
        </MotionText>

        {/* Subtitle with fade-in */}
        <MotionSubtext
          className="max-w-prose px-4 pt-2 text-balance text-lg leading-6 font-medium text-white/95 md:text-xl"
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{
            duration: 0.6,
            ease: [0.16, 1, 0.3, 1],
            delay: 0.8,
          }}
        >
          Genie at your service—adding groceries, munchies, or SOS supplies with superfast delivery
          promises.
        </MotionSubtext>

        {/* Pill badge with slide-up */}
        <motion.div
          className="mt-6 rounded-full bg-white px-6 py-1.5 text-xs font-semibold uppercase tracking-wide text-[#4D148C] shadow-sm"
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{
            duration: 0.5,
            ease: [0.16, 1, 0.3, 1],
            delay: 1,
          }}
        >
          Free delivery above ₹199 · Late-night Basket cravings covered
        </motion.div>

        {/* Button with bounce-in */}
        <motion.div
          initial={{ opacity: 0, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{
            duration: 0.5,
            ease: [0.16, 1, 0.3, 1],
            delay: 1.2,
          }}
        >
          <Button
            variant="primary"
            size="lg"
            onClick={onStartCall}
            className="mt-8 w-64 border-none bg-white font-mono text-[#ff5200] transition-all hover:scale-105 hover:bg-gray-100"
          >
            {startButtonText}
          </Button>
        </motion.div>
      </section>
    </div>
  );
};