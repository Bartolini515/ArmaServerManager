import {
	applyPolledTask,
	beginPendingInstanceRequest,
	decodeTaskStatus,
	finishPendingInstanceRequest,
	createReconciliationRetryController,
	getShutdownAt,
	handleTaskPollError,
	isInstanceActionBlocked,
	isInstanceRequestBlocked,
	mergeServerTaskBindings,
	releaseReconciledBlocks,
	setReconciliationBlock,
	shouldRetryReconciliation,
	shouldAcceptListSnapshot,
	setTrackedTask,
	withReconciliationRetry,
} from "./taskState.ts";
import type { Instance, TaskLedger, TrackedTask } from "./types.ts";

class ControlledTimers {
	private nextId = 1;
	private readonly timers = new Map<
		number,
		{ dueAt: number; callback: () => void }
	>();
	now = 0;

	schedule = (callback: () => void, delay: number): number => {
		const id = this.nextId;
		this.nextId += 1;
		this.timers.set(id, { dueAt: this.now + delay, callback });
		return id;
	};

	cancel = (id: number): void => {
		this.timers.delete(id);
	};

	advance = (duration: number): void => {
		const targetTime = this.now + duration;
		while (true) {
			const next = [...this.timers.entries()]
				.filter(([, timer]) => timer.dueAt <= targetTime)
				.sort((left, right) => left[1].dueAt - right[1].dueAt)[0];
			if (!next) {
				break;
			}
			const [id, timer] = next;
			this.timers.delete(id);
			this.now = timer.dueAt;
			timer.callback();
		}
		this.now = targetTime;
	};

	get pendingCount(): number {
		return this.timers.size;
	}
}

function deferred<T>() {
	let resolve!: (value: T) => void;
	let reject!: (error: unknown) => void;
	const promise = new Promise<T>((resolvePromise, rejectPromise) => {
		resolve = resolvePromise;
		reject = rejectPromise;
	});
	return { promise, resolve, reject };
}

function normalize(value: unknown): unknown {
	if (Array.isArray(value)) {
		return value.map(normalize);
	}
	if (typeof value === "object" && value !== null) {
		return Object.fromEntries(
			Object.keys(value)
				.sort()
				.map((key) => [key, normalize((value as Record<string, unknown>)[key])]),
		);
	}
	return value;
}

const assert = {
	equal(actual: unknown, expected: unknown, label = "values should match") {
		if (actual !== expected) {
			throw new Error(`${label}: expected ${String(expected)}, received ${String(actual)}`);
		}
	},
	deepEqual(actual: unknown, expected: unknown, label = "objects should match") {
		const actualText = JSON.stringify(normalize(actual));
		const expectedText = JSON.stringify(normalize(expected));
		if (actualText !== expectedText) {
			throw new Error(`${label}: expected ${expectedText}, received ${actualText}`);
		}
	},
};

const pendingTask: TrackedTask = {
	taskId: "task-pending",
	state: "PENDING",
	status: "Zadanie oczekuje w kolejce",
};

function makeInstance(
	values: Partial<Instance> & Pick<Instance, "id" | "name">,
): Instance {
	const instance: Instance = {
		id: values.id,
		name: values.name,
		user: { id: 1, username: "fixture-owner", last_login: "" },
		preset: "preset.html",
		log_file: null,
		start_file_path: "",
		port: "2302",
		pid: 0,
		is_ready: true,
		is_running: false,
		is_admin_instance: false,
		created_at: "",
	};
	return Object.assign(instance, values);
}

function addTask(ledger: TaskLedger, instanceId: number, task: TrackedTask) {
	return setTrackedTask(ledger, instanceId, "start", task);
}

const nullPending = decodeTaskStatus(null, pendingTask);
assert.deepEqual(nullPending, {
	kind: "active",
	task: pendingTask,
});
assert.deepEqual(
	decodeTaskStatus({ state: "PENDING", result: null }, pendingTask),
	{ kind: "active", task: pendingTask },
);

assert.deepEqual(
	decodeTaskStatus(
		{ state: "PROGRESS", result: { status: "Pobieranie 2/5" } },
		pendingTask,
	),
	{
		kind: "active",
		task: {
			taskId: "task-pending",
			state: "PROGRESS",
			status: "Pobieranie 2/5",
		},
	},
);
assert.equal(
	decodeTaskStatus({ state: "RETRY", result: null }, pendingTask).kind,
	"active",
);
assert.equal(
	decodeTaskStatus({ state: "BACKOFF", result: null }, pendingTask).kind,
	"active",
);
assert.equal(
	decodeTaskStatus({ state: "REVOKED", result: null }, pendingTask).kind,
	"terminal",
);

let ledger: TaskLedger = addTask({}, 8, pendingTask);
const outage = handleTaskPollError(ledger, 8, "start", pendingTask.taskId, 503);
assert.equal(outage.kind, "retry");
assert.equal(outage.ledger[8]?.start?.taskId, pendingTask.taskId);
const recovered = applyPolledTask(
	outage.ledger,
	8,
	"start",
	pendingTask.taskId,
	decodeTaskStatus({ state: "STARTED", result: { status: "Uruchamianie" } }, pendingTask),
);
assert.equal(recovered.applied, true);
assert.equal(recovered.ledger[8]?.start?.state, "STARTED");

const obsoleteTask = handleTaskPollError(
	recovered.ledger,
	8,
	"start",
	pendingTask.taskId,
	404,
);
assert.equal(obsoleteTask.kind, "retired");
assert.equal(obsoleteTask.ledger[8]?.start, undefined);

const firstTerminal = applyPolledTask(
	recovered.ledger,
	8,
	"start",
	pendingTask.taskId,
	decodeTaskStatus({ state: "SUCCESS", result: { status: "Gotowe" } }, pendingTask),
);
assert.equal(firstTerminal.applied, true);
assert.equal(firstTerminal.ended, true);
assert.equal(firstTerminal.ledger[8]?.start, undefined);
const duplicateTerminal = applyPolledTask(
	firstTerminal.ledger,
	8,
	"start",
	pendingTask.taskId,
	decodeTaskStatus({ state: "SUCCESS", result: { status: "Gotowe" } }, pendingTask),
);
assert.equal(duplicateTerminal.applied, false);

const replacement = { ...pendingTask, taskId: "task-new" };
ledger = addTask(ledger, 8, replacement);
const staleResponse = applyPolledTask(
	ledger,
	8,
	"start",
	pendingTask.taskId,
	decodeTaskStatus({ state: "SUCCESS", result: null }, pendingTask),
);
assert.equal(staleResponse.applied, false);
assert.equal(staleResponse.ledger[8]?.start?.taskId, "task-new");

const serverRecovered = mergeServerTaskBindings(
	{},
	[
		makeInstance({
			id: 9,
			name: "recovered-instance",
			active_tasks: { stop: "task-server-bound" },
		}),
	],
	new Set(),
);
assert.equal(serverRecovered[9]?.stop?.taskId, "task-server-bound");
assert.equal(serverRecovered[9]?.stop?.state, "PENDING");
const completedBindingIgnored = mergeServerTaskBindings(
	serverRecovered,
	[
		makeInstance({
			id: 9,
			name: "recovered-instance",
			active_tasks: { stop: "task-server-bound" },
		}),
	],
	new Set(["task-server-bound"]),
);
assert.equal(completedBindingIgnored[9]?.stop, undefined);

const busyInstance = makeInstance({
	id: 10,
	name: "busy-instance",
	lifecycle_state: "starting",
});
assert.equal(
	isInstanceActionBlocked(busyInstance, {}, false),
	true,
);
assert.equal(
	isInstanceActionBlocked(
		{ ...busyInstance, lifecycle_state: "stopped" },
		{ start: pendingTask },
		false,
	),
	true,
);

let reconciliationBlocks = setReconciliationBlock(new Map(), 20, 40, 3);
assert.equal(isInstanceRequestBlocked(new Set(), reconciliationBlocks, 20), true);
assert.equal(shouldRetryReconciliation(reconciliationBlocks), true);
const beforeEventSnapshot = releaseReconciledBlocks(reconciliationBlocks, 40, 3);
assert.equal(beforeEventSnapshot.has(20), true);
const staleGenerationSnapshot = releaseReconciledBlocks(reconciliationBlocks, 41, 2);
assert.equal(staleGenerationSnapshot.has(20), true);
// A failed refresh does not call releaseReconciledBlocks, so the instance stays blocked.
assert.equal(reconciliationBlocks.has(20), true);
assert.equal(shouldRetryReconciliation(reconciliationBlocks), true);
reconciliationBlocks = releaseReconciledBlocks(reconciliationBlocks, 41, 3);
assert.equal(reconciliationBlocks.has(20), false);
assert.equal(shouldRetryReconciliation(reconciliationBlocks), false);
assert.equal(isInstanceRequestBlocked(new Set(), reconciliationBlocks, 20), false);

const delayedListGeneration = 4;
let currentOperationGeneration = delayedListGeneration;
let latestListRequestId = 52;
const delayedSnapshotRequestId = latestListRequestId;
const delayedLedger = setTrackedTask({}, 21, "start", {
	...pendingTask,
	taskId: "task-A",
});
const newerJobLedger = setTrackedTask(delayedLedger, 21, "start", {
	...pendingTask,
	taskId: "task-B",
});
currentOperationGeneration += 1;
latestListRequestId += 1;
const oldListIsAccepted = shouldAcceptListSnapshot(
	delayedSnapshotRequestId,
	latestListRequestId,
	delayedListGeneration,
	currentOperationGeneration,
);
assert.equal(oldListIsAccepted, false);
const ledgerAfterDelayedList = oldListIsAccepted
	? mergeServerTaskBindings(
			newerJobLedger,
			[
				makeInstance({
					id: 21,
					name: "instance-with-old-binding",
					active_tasks: { start: "task-A" },
				}),
			],
			new Set(),
		)
	: newerJobLedger;
assert.equal(ledgerAfterDelayedList[21]?.start?.taskId, "task-B");

let sharedPendingRequests: ReadonlySet<number> = new Set();
const firstLogDelete = beginPendingInstanceRequest(sharedPendingRequests, 22);
assert.equal(firstLogDelete.started, true);
sharedPendingRequests = firstLogDelete.pending;
const duplicateLogDelete = beginPendingInstanceRequest(sharedPendingRequests, 22);
assert.equal(duplicateLogDelete.started, false);
sharedPendingRequests = duplicateLogDelete.pending;
// Closing the modal does not change the parent's shared per-instance pending state.
assert.equal(isInstanceRequestBlocked(sharedPendingRequests, new Map(), 22), true);
const logDeleteInstance = makeInstance({ id: 22, name: "logs-pending" });
assert.equal(
	isInstanceActionBlocked(
		logDeleteInstance,
		{},
		isInstanceRequestBlocked(sharedPendingRequests, new Map(), 22),
	),
	true,
);
reconciliationBlocks = setReconciliationBlock(new Map(), 22, 70, 8);
sharedPendingRequests = finishPendingInstanceRequest(sharedPendingRequests, 22);
assert.equal(isInstanceRequestBlocked(sharedPendingRequests, reconciliationBlocks, 22), true);
assert.equal(
	isInstanceActionBlocked(
		logDeleteInstance,
		{},
		isInstanceRequestBlocked(sharedPendingRequests, reconciliationBlocks, 22),
	),
	true,
);
reconciliationBlocks = releaseReconciledBlocks(reconciliationBlocks, 71, 8);
assert.equal(isInstanceRequestBlocked(sharedPendingRequests, reconciliationBlocks, 22), false);

const expiredA = "2031-01-02T10:00:00Z";
const expiredB = "2031-01-02T12:00:00Z";
assert.equal(
	getShutdownAt(
		makeInstance({
			id: 11,
			name: "owner-a",
			is_running: true,
			lifecycle_state: "running",
			shutdown_at: expiredA,
		}),
	),
	expiredA,
);
assert.equal(
	getShutdownAt(
		makeInstance({
			id: 12,
			name: "owner-b",
			is_running: true,
			lifecycle_state: "running",
			shutdown_at: expiredB,
		}),
	),
	expiredB,
);
assert.equal(
	getShutdownAt(
		makeInstance({
			id: 13,
			name: "admin-no-deadline",
			is_admin_instance: true,
			is_running: true,
			lifecycle_state: "running",
			shutdown_at: null,
		}),
	),
	null,
);
assert.equal(
	getShutdownAt(
		makeInstance({
			id: 15,
			name: "old-fixture-without-deadline",
			is_running: true,
		}),
	),
	null,
);
assert.equal(
	getShutdownAt(
		makeInstance({
			id: 14,
			name: "invalid-deadline",
			is_running: true,
			lifecycle_state: "running",
			shutdown_at: "not-a-date",
		}),
	),
	null,
);
assert.equal(
	getShutdownAt(
		makeInstance({
			id: 16,
			name: "impossible-calendar-date",
			is_running: true,
			lifecycle_state: "running",
			shutdown_at: "2031-02-30T10:00:00Z",
		}),
	),
	null,
);
for (const [id, offset] of [
	[17, "+05:30"],
	[18, "+05:45"],
	[19, "-03:30"],
] as const) {
	const deadline = `2031-01-02T10:00:00${offset}`;
	assert.equal(
		getShutdownAt(
			makeInstance({
				id,
				name: `offset-${offset}`,
				is_running: true,
				lifecycle_state: "running",
				shutdown_at: deadline,
			}),
		),
		deadline,
		`valid timezone offset ${offset} should retain the shutdown deadline`,
	);
}
for (const [id, offset] of [
	[20, "+24:00"],
	[21, "+05:60"],
] as const) {
	assert.equal(
		getShutdownAt(
			makeInstance({
				id,
				name: `invalid-offset-${offset}`,
				is_running: true,
				lifecycle_state: "running",
				shutdown_at: `2031-01-02T10:00:00${offset}`,
			}),
		),
		null,
		`invalid timezone offset ${offset} should be rejected`,
	);
}

const slowTimers = new ControlledTimers();
const slowRefreshBlocks = setReconciliationBlock(new Map(), 30, 100, 4);
const slowRefresh = deferred<string>();
let slowRequestCount = 0;
let slowConcurrentCount = 0;
let slowMaxConcurrent = 0;
let latestSlowListRequestId = 1;
let acceptedSlowSnapshot: string | null = null;
const slowRetry = createReconciliationRetryController(
	slowTimers.schedule,
	slowTimers.cancel,
	() => {
		slowRequestCount += 1;
		latestSlowListRequestId += 1;
	},
	5000,
);
const slowRefreshResult = withReconciliationRetry(
	() => {
		slowRequestCount += 1;
		slowConcurrentCount += 1;
		slowMaxConcurrent = Math.max(slowMaxConcurrent, slowConcurrentCount);
		return slowRefresh.promise.finally(() => {
			slowConcurrentCount -= 1;
		});
	},
	slowRetry,
	() => shouldRetryReconciliation(slowRefreshBlocks),
).then((snapshot) => {
	if (shouldAcceptListSnapshot(1, latestSlowListRequestId, 4, 4)) {
		acceptedSlowSnapshot = snapshot;
	}
	return snapshot;
});
slowTimers.advance(5000);
assert.equal(slowRequestCount, 1);
assert.equal(slowTimers.pendingCount, 0);
slowTimers.advance(1000);
slowRefresh.resolve("accepted after six seconds");
assert.equal(await slowRefreshResult, "accepted after six seconds");
assert.equal(slowRequestCount, 1);
assert.equal(slowMaxConcurrent, 1);
assert.equal(acceptedSlowSnapshot, "accepted after six seconds");

const outageTimers = new ControlledTimers();
let outageBlocks = setReconciliationBlock(new Map(), 31, 200, 5);
let outageRequestCount = 0;
let outageConcurrentCount = 0;
let outageMaxConcurrent = 0;
const outageAttempts: Array<{
	response: ReturnType<typeof deferred<string>>;
	promise: Promise<string>;
}> = [];
function requestWithRetry(): void {
	const response = deferred<string>();
	const retry = createReconciliationRetryController(
		outageTimers.schedule,
		outageTimers.cancel,
		() => {
			if (shouldRetryReconciliation(outageBlocks)) {
				requestWithRetry();
			}
		},
		5000,
	);
	const promise = withReconciliationRetry(
		() => {
			outageRequestCount += 1;
			outageConcurrentCount += 1;
			outageMaxConcurrent = Math.max(outageMaxConcurrent, outageConcurrentCount);
			return response.promise.finally(() => {
				outageConcurrentCount -= 1;
			});
		},
		retry,
		() => shouldRetryReconciliation(outageBlocks),
	);
	outageAttempts.push({ response, promise });
}
requestWithRetry();
outageAttempts[0].response.reject(new Error("synthetic list outage"));
await outageAttempts[0].promise.catch(() => undefined);
assert.equal(outageTimers.pendingCount, 1);
outageTimers.advance(4999);
assert.equal(outageRequestCount, 1);
outageTimers.advance(1);
assert.equal(outageRequestCount, 2);
assert.equal(outageAttempts.length, 2);
assert.equal(outageConcurrentCount, 1);
outageAttempts[1].response.resolve("recovered snapshot");
assert.equal(await outageAttempts[1].promise, "recovered snapshot");
assert.equal(outageMaxConcurrent, 1);
assert.equal(outageTimers.pendingCount, 0);
outageBlocks = releaseReconciledBlocks(outageBlocks, 201, 5);
assert.equal(shouldRetryReconciliation(outageBlocks), false);

console.log("taskState regression tests passed");
