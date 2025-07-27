import { useState, useCallback, useEffect, useRef } from "react";

interface UseCountdownOptions {
	duration: number; // Duration in milliseconds
	interval?: number; // Update interval in milliseconds (default: 1000)
	onComplete?: () => void; // Callback when countdown reaches zero
}

export interface UseCountdownReturn {
	timeLeft: number; // Remaining time in milliseconds
	isRunning: boolean;
	start: () => void;
	pause: () => void;
	resume: () => void;
	reset: () => void;
}

export function useCountdown({
	duration,
	interval = 1000,
	onComplete,
}: UseCountdownOptions): UseCountdownReturn {
	const [timeLeft, setTimeLeft] = useState(duration);
	const [isRunning, setIsRunning] = useState(false);
	const savedCallback = useRef<() => void>();

	useEffect(() => {
		setTimeLeft(duration);
	}, [duration]);

	const tick = useCallback(() => {
		setTimeLeft((prevTime) => {
			const newTime = Math.max(0, prevTime - interval);
			if (newTime === 0) {
				setIsRunning(false);
				onComplete?.();
			}
			return newTime;
		});
	}, [interval, onComplete]);

	// Store the latest tick function
	useEffect(() => {
		savedCallback.current = tick;
	}, [tick]);

	// Integrated interval logic
	useEffect(() => {
		function tickWrapper() {
			savedCallback.current?.();
		}

		if (isRunning) {
			const id = setInterval(tickWrapper, interval);
			return () => {
				clearInterval(id);
			};
		}
	}, [isRunning, interval]);

	const start = useCallback(() => {
		setIsRunning(true);
	}, []);

	const pause = useCallback(() => {
		setIsRunning(false);
	}, []);

	const resume = useCallback(() => {
		setIsRunning(true);
	}, []);

	const reset = useCallback(() => {
		setTimeLeft(duration);
		setIsRunning(false);
	}, [duration]);

	return {
		timeLeft,
		isRunning,
		start,
		pause,
		resume,
		reset,
	};
}

// Utility functions for common time conversions
export const timeUtils = {
	seconds: (s: number) => s * 1000,
	minutes: (m: number) => m * 60 * 1000,
	hours: (h: number) => h * 60 * 60 * 1000,
	formatTime: (ms: number) => {
		const totalSeconds = Math.floor(ms / 1000);
		const hours = Math.floor(totalSeconds / 3600);
		const minutes = Math.floor((totalSeconds % 3600) / 60);
		const seconds = totalSeconds % 60;

		if (hours > 0) {
			return `${hours}:${minutes.toString().padStart(2, "0")}:${seconds
				.toString()
				.padStart(2, "0")}`;
		}
		return `${minutes}:${seconds.toString().padStart(2, "0")}`;
	},
};
