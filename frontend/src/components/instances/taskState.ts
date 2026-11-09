import {
	TASK_KINDS,
	type Instance,
	type LifecycleState,
	type TaskKind,
	type TaskLedger,
	type TrackedTask,
} from "./types.ts";

export type DecodedTaskStatus =
	| { kind: "active"; task: TrackedTask }
	| { kind: "terminal"; task: TrackedTask };

export type AppliedPoll = {
	ledger: TaskLedger;
	applied: boolean;
	ended: boolean;
	task?: TrackedTask;
};

export interface ReconciliationBlock {
	afterRequestId: number;
	operationGeneration: number;
}

export type ReconciliationBlocks = ReadonlyMap<number, ReconciliationBlock>;

export type TaskPollError =
	| { kind: "retry"; ledger: TaskLedger }
	| { kind: "retired"; ledger: TaskLedger }
	| { kind: "stale"; ledger: TaskLedger };

const TERMINAL_STATES = new Set(["SUCCESS", "FAILURE", "REVOKED"]);
const LIFECYCLE_STATES = new Set<LifecycleState>([
	"stopped",
	"starting",
	"running",
	"stopping",
	"unknown",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function getTaskStatus(result: unknown): string | undefined {
	if (typeof result === "string" && result.trim()) {
		return result;
	}
	if (!isRecord(result)) {
		return undefined;
	}
	for (const key of ["status", "detail", "message"]) {
		const value = result[key];
		if (typeof value === "string" && value.trim()) {
			return value;
		}
	}
	return undefined;
}

function fallbackTaskStatus(state: string, previous: string): string {
	if (previous) {
		return previous;
	}
	switch (state.toUpperCase()) {
		case "PENDING":
			return "Zadanie oczekuje w kolejce";
		case "STARTED":
			return "Zadanie zostało rozpoczęte";
		case "RETRY":
			return "Ponawianie zadania";
		case "SUCCESS":
			return "Zadanie zakończone pomyślnie";
		case "FAILURE":
			return "Zadanie zakończone błędem";
		case "REVOKED":
			return "Zadanie zostało anulowane";
		default:
			return state;
	}
}

export function decodeTaskStatus(
	payload: unknown,
	previous: TrackedTask,
): DecodedTaskStatus {
	let state = previous.state || "PENDING";
	let result: unknown;
	if (isRecord(payload)) {
		if (typeof payload.state === "string" && payload.state.trim()) {
			state = payload.state;
		}
		result = payload.result;
	}
	const status = getTaskStatus(result) ?? fallbackTaskStatus(state, previous.status);
	const task = { ...previous, state, status };
	return TERMINAL_STATES.has(state.toUpperCase())
		? { kind: "terminal", task }
		: { kind: "active", task };
}

export function readTaskId(value: unknown): string | null {
	if (!isRecord(value) || typeof value.task_id !== "string") {
		return null;
	}
	const taskId = value.task_id.trim();
	return taskId ? taskId : null;
}

export function setTrackedTask(
	ledger: TaskLedger,
	instanceId: number,
	kind: TaskKind,
	task: TrackedTask,
): TaskLedger {
	return {
		...ledger,
		[instanceId]: {
			...ledger[instanceId],
			[kind]: task,
		},
	};
}

export function beginPendingInstanceRequest(
	pending: ReadonlySet<number>,
	instanceId: number,
): { started: boolean; pending: ReadonlySet<number> } {
	if (pending.has(instanceId)) {
		return { started: false, pending };
	}
	const next = new Set(pending);
	next.add(instanceId);
	return { started: true, pending: next };
}

export function finishPendingInstanceRequest(
	pending: ReadonlySet<number>,
	instanceId: number,
): ReadonlySet<number> {
	if (!pending.has(instanceId)) {
		return pending;
	}
	const next = new Set(pending);
	next.delete(instanceId);
	return next;
}

export function setReconciliationBlock(
	blocks: ReconciliationBlocks,
	instanceId: number,
	afterRequestId: number,
	operationGeneration: number,
): ReconciliationBlocks {
	const previous = blocks.get(instanceId);
	const next = new Map(blocks);
	next.set(instanceId, {
		afterRequestId: Math.max(previous?.afterRequestId ?? -1, afterRequestId),
		operationGeneration: Math.max(
			previous?.operationGeneration ?? -1,
			operationGeneration,
		),
	});
	return next;
}

export function releaseReconciledBlocks(
	blocks: ReconciliationBlocks,
	requestId: number,
	operationGeneration: number,
): ReconciliationBlocks {
	let next: Map<number, ReconciliationBlock> | undefined;
	for (const [instanceId, block] of blocks) {
		if (
			requestId > block.afterRequestId &&
			operationGeneration >= block.operationGeneration
		) {
			next ??= new Map(blocks);
			next.delete(instanceId);
		}
	}
	return next ?? blocks;
}

export function isInstanceRequestBlocked(
	pending: ReadonlySet<number>,
	blocks: ReconciliationBlocks,
	instanceId: number,
): boolean {
	return pending.has(instanceId) || blocks.has(instanceId);
}

export function shouldRetryReconciliation(blocks: ReconciliationBlocks): boolean {
	return blocks.size > 0;
}

export interface ReconciliationRetryController {
	scheduleAfterFailure: () => void;
	cancel: () => void;
	dispose: () => void;
}

export function createReconciliationRetryController<Timer>(
	schedule: (callback: () => void, delay: number) => Timer,
	cancelTimer: (timer: Timer) => void,
	retry: () => void,
	delay: number,
): ReconciliationRetryController {
	let timer: Timer | null = null;
	let disposed = false;
	const cancel = () => {
		if (timer !== null) {
			cancelTimer(timer);
			timer = null;
		}
	};
	return {
		scheduleAfterFailure: () => {
			if (disposed || timer !== null) {
				return;
			}
			timer = schedule(() => {
				timer = null;
				if (!disposed) {
					retry();
				}
			}, delay);
		},
		cancel,
		dispose: () => {
			disposed = true;
			cancel();
		},
	};
}

export async function withReconciliationRetry<T>(
	attempt: () => Promise<T>,
	controller: ReconciliationRetryController,
	shouldRetry: () => boolean,
): Promise<T> {
	try {
		const value = await attempt();
		controller.cancel();
		return value;
	} catch (error: unknown) {
		if (shouldRetry()) {
			controller.scheduleAfterFailure();
		}
		throw error;
	}
}

export function shouldAcceptListSnapshot(
	snapshotRequestId: number,
	latestRequestId: number,
	snapshotOperationGeneration: number,
	currentOperationGeneration: number,
): boolean {
	return (
		snapshotRequestId === latestRequestId &&
		snapshotOperationGeneration === currentOperationGeneration
	);
}

function isCurrentTask(
	ledger: TaskLedger,
	instanceId: number,
	kind: TaskKind,
	taskId: string,
): boolean {
	return ledger[instanceId]?.[kind]?.taskId === taskId;
}

function removeCurrentTask(
	ledger: TaskLedger,
	instanceId: number,
	kind: TaskKind,
): TaskLedger {
	const current = ledger[instanceId];
	if (!current) {
		return ledger;
	}
	const nextInstance = { ...current };
	delete nextInstance[kind];
	const nextLedger = { ...ledger };
	if (Object.keys(nextInstance).length === 0) {
		delete nextLedger[instanceId];
	} else {
		nextLedger[instanceId] = nextInstance;
	}
	return nextLedger;
}

export function applyPolledTask(
	ledger: TaskLedger,
	instanceId: number,
	kind: TaskKind,
	taskId: string,
	decoded: DecodedTaskStatus,
): AppliedPoll {
	if (!isCurrentTask(ledger, instanceId, kind, taskId)) {
		return { ledger, applied: false, ended: false };
	}
	if (decoded.kind === "terminal") {
		return {
			ledger: removeCurrentTask(ledger, instanceId, kind),
			applied: true,
			ended: true,
			task: decoded.task,
		};
	}
	return {
		ledger: setTrackedTask(ledger, instanceId, kind, decoded.task),
		applied: true,
		ended: false,
		task: decoded.task,
	};
}

export function handleTaskPollError(
	ledger: TaskLedger,
	instanceId: number,
	kind: TaskKind,
	taskId: string,
	status: number | undefined,
): TaskPollError {
	if (!isCurrentTask(ledger, instanceId, kind, taskId)) {
		return { kind: "stale", ledger };
	}
	if (status === 404) {
		return {
			kind: "retired",
			ledger: removeCurrentTask(ledger, instanceId, kind),
		};
	}
	return { kind: "retry", ledger };
}

const INITIAL_STATUS: Record<TaskKind, string> = {
	start: "Rozpoczęto uruchamianie",
	stop: "Rozpoczęto zatrzymywanie",
	download: "Rozpoczęto pobieranie",
};

function readBindings(value: unknown): Partial<Record<TaskKind, string>> {
	if (!isRecord(value)) {
		return {};
	}
	const bindings: Partial<Record<TaskKind, string>> = {};
	for (const kind of TASK_KINDS) {
		const taskId = value[kind];
		if (typeof taskId === "string" && taskId.trim()) {
			bindings[kind] = taskId.trim();
		}
	}
	return bindings;
}

export function mergeServerTaskBindings(
	ledger: TaskLedger,
	instances: readonly Instance[],
	completedOrRetiredTaskIds: ReadonlySet<string>,
): TaskLedger {
	let nextLedger = ledger;
	for (const instance of instances) {
		for (const [kind, taskId] of Object.entries(
			readBindings(instance.active_tasks),
		) as [TaskKind, string][]) {
			if (completedOrRetiredTaskIds.has(taskId)) {
				if (nextLedger[instance.id]?.[kind]?.taskId === taskId) {
					nextLedger = removeCurrentTask(nextLedger, instance.id, kind);
				}
				continue;
			}
			const current = nextLedger[instance.id]?.[kind];
			if (current?.taskId === taskId) {
				continue;
			}
			nextLedger = setTrackedTask(nextLedger, instance.id, kind, {
				taskId,
				state: "PENDING",
				status: INITIAL_STATUS[kind],
			});
		}
	}
	return nextLedger;
}

export function getLifecycleState(instance: Instance): LifecycleState {
	const lifecycleState: unknown = instance.lifecycle_state;
	if (lifecycleState === undefined) {
		return instance.is_running ? "running" : "stopped";
	}
	if (
		typeof lifecycleState === "string" &&
		LIFECYCLE_STATES.has(lifecycleState as LifecycleState)
	) {
		return lifecycleState as LifecycleState;
	}
	return "unknown";
}

export function getShutdownAt(instance: Instance): string | null {
	if (getLifecycleState(instance) !== "running") {
		return null;
	}
	const shutdownAt: unknown = instance.shutdown_at;
	if (typeof shutdownAt !== "string" || !shutdownAt.trim()) {
		return null;
	}
	const value = shutdownAt.trim();
	const match = value.match(
		/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(Z|([+-])(\d{2}):(\d{2}))$/,
	);
	if (!match) {
		return null;
	}
	const year = Number(match[1]);
	const month = Number(match[2]);
	const day = Number(match[3]);
	const hour = Number(match[4]);
	const minute = Number(match[5]);
	const second = Number(match[6]);
	const leapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
	const daysInMonth = [
		31,
		leapYear ? 29 : 28,
		31,
		30,
		31,
		30,
		31,
		31,
		30,
		31,
		30,
		31,
	][month - 1];
	const offsetHour = Number(match[9] ?? 0);
	const offsetMinute = Number(match[10] ?? 0);
	if (
		!daysInMonth ||
		day < 1 ||
		day > daysInMonth ||
		hour > 23 ||
		minute > 59 ||
		second > 59 ||
		offsetHour > 23 ||
		offsetMinute > 59
	) {
		return null;
	}
	return Number.isFinite(Date.parse(value)) ? value : null;
}

export function getOwnerName(instance: Instance): string {
	const user: unknown = instance.user;
	if (isRecord(user) && typeof user.username === "string" && user.username.trim()) {
		return user.username;
	}
	return "Nieznany użytkownik";
}

export function isInstanceActionBlocked(
	instance: Instance,
	activeTasks: Partial<Record<TaskKind, TrackedTask>>,
	requestPending: boolean,
): boolean {
	if (requestPending || Object.keys(activeTasks).length > 0) {
		return true;
	}
	const lifecycleState = getLifecycleState(instance);
	return lifecycleState === "starting" || lifecycleState === "stopping" || lifecycleState === "unknown";
}
