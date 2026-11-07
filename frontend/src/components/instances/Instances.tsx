import { useCallback, useEffect, useRef, useState } from "react";
import { Divider, Skeleton, Stack, Typography } from "@mui/material";
import Box from "@mui/material/Box";
import AxiosInstance from "../AxiosInstance";
import { useAuth } from "../../contexts/AuthContext";
import { useAlert } from "../../contexts/AlertContext";
import FAB from "../../UI/forms/FAB";
import CreateInstanceModal from "./modals/CreateInstanceModal";
import ServerLogsModal from "./modals/ServerLogsModal";
import ChangePresetModal from "./modals/ChangePresetModal";
import { getApiErrorMessage, getApiErrorStatus } from "../../util/api-error";
import { useInterval } from "../../hooks/use-interval";
import AdminInstances from "./AdminInstances";
import UserInstances from "./UserInstances";
import {
	applyPolledTask,
	beginPendingInstanceRequest,
	createReconciliationRetryController,
	decodeTaskStatus,
	finishPendingInstanceRequest,
	handleTaskPollError,
	isInstanceRequestBlocked,
	mergeServerTaskBindings,
	readTaskId,
	getLifecycleState,
	isInstanceActionBlocked,
	releaseReconciledBlocks,
	setReconciliationBlock,
	setTrackedTask,
	shouldRetryReconciliation,
	shouldAcceptListSnapshot,
	withReconciliationRetry,
	type ReconciliationBlocks,
} from "./taskState";
import {
	TASK_KINDS,
	type Instance,
	type InstanceAction,
	type InstanceListResponse,
	type TaskKind,
	type TaskLedger,
	type TrackedTask,
} from "./types";

const RECONCILIATION_RETRY_MS = 5000;

const TASK_ACTIONS: Record<
	TaskKind,
	{ success: string; failure: string; initialStatus: string }
> = {
	start: {
		success: "Instancja została uruchomiona pomyślnie!",
		failure: "Uruchomienie instancji nie powiodło się:",
		initialStatus: "Rozpoczęto uruchamianie",
	},
	stop: {
		success: "Instancja została zatrzymana pomyślnie!",
		failure: "Zatrzymanie instancji nie powiodło się:",
		initialStatus: "Rozpoczęto zatrzymywanie",
	},
	download: {
		success: "Pobieranie zakończone pomyślnie!",
		failure: "Pobieranie nie powiodło się:",
		initialStatus: "Rozpoczęto pobieranie",
	},
};

function isInstanceArray(value: unknown): value is Instance[] {
	return Array.isArray(value);
}

function getTaskActionPath(action: TaskKind): string {
	return action === "download" ? "download_mods" : action;
}

export default function Instances() {
	const [openCreateInstanceModal, setOpenCreateInstanceModal] = useState(false);
	const [userInstances, setUserInstances] = useState<Instance[]>([]);
	const [adminInstances, setAdminInstances] = useState<Instance[]>([]);
	const [otherInstances, setOtherInstances] = useState<Instance[]>([]);
	const [loading, setLoading] = useState(true);
	const [refreshVersion, setRefreshVersion] = useState(0);
	const [tasks, setTasks] = useState<TaskLedger>({});
	const [pendingRequests, setPendingRequests] = useState<ReadonlySet<number>>(
		new Set(),
	);
	const [reconciliationBlocks, setReconciliationBlocks] =
		useState<ReconciliationBlocks>(new Map());
	const [selectedInstanceId, setSelectedInstanceId] = useState<number | null>(
		null,
	);
	const [openChangePresetModal, setOpenChangePresetModal] = useState(false);
	const [selectedAdminInstanceId, setSelectedAdminInstanceId] = useState<
		number | null
	>(null);

	const { isAdmin, token } = useAuth();
	const { setAlert } = useAlert();
	const tasksRef = useRef<TaskLedger>({});
	const pendingRequestsRef = useRef<ReadonlySet<number>>(new Set());
	const reconciliationBlocksRef = useRef<ReconciliationBlocks>(new Map());
	const completedOrRetiredTaskIdsRef = useRef(new Set<string>());
	const pollingRef = useRef(false);
	const mountedRef = useRef(false);
	const tokenRef = useRef(token);
	const listRequestIdRef = useRef(0);
	const operationGenerationRef = useRef(0);
	tokenRef.current = token;

	const commitTasks = useCallback((nextTasks: TaskLedger) => {
		tasksRef.current = nextTasks;
		setTasks(nextTasks);
	}, []);

	const commitPendingRequests = useCallback((nextRequests: ReadonlySet<number>) => {
		pendingRequestsRef.current = nextRequests;
		setPendingRequests(nextRequests);
	}, []);

	const commitReconciliationBlocks = useCallback(
		(nextBlocks: ReconciliationBlocks) => {
			reconciliationBlocksRef.current = nextBlocks;
			setReconciliationBlocks(nextBlocks);
		},
		[],
	);

	const markRequestPending = useCallback(
		(instanceId: number, pending: boolean): boolean => {
			if (pending) {
				const started = beginPendingInstanceRequest(
					pendingRequestsRef.current,
					instanceId,
				);
				if (!started.started) {
					return false;
				}
				commitPendingRequests(started.pending);
				return true;
			}
			commitPendingRequests(
				finishPendingInstanceRequest(
					pendingRequestsRef.current,
					instanceId,
				),
			);
			return true;
		},
		[commitPendingRequests],
	);

	const requestRefresh = useCallback(() => {
		setRefreshVersion((version) => version + 1);
	}, []);

	const requireReconciliation = useCallback(
		(instanceId: number) => {
			operationGenerationRef.current += 1;
			commitReconciliationBlocks(
				setReconciliationBlock(
					reconciliationBlocksRef.current,
					instanceId,
					listRequestIdRef.current,
					operationGenerationRef.current,
				),
			);
			requestRefresh();
		},
		[commitReconciliationBlocks, requestRefresh],
	);

	const findInstanceById = useCallback(
		(instanceId: number) =>
			[...adminInstances, ...userInstances, ...otherInstances].find(
				(instance) => instance.id === instanceId,
			),
		[adminInstances, otherInstances, userInstances],
	);

	const canStartCardAction = useCallback(
		(type: Exclude<InstanceAction, "logs">, instanceId: number) => {
			const instance = findInstanceById(instanceId);
			if (
				!instance ||
				isInstanceRequestBlocked(
					pendingRequestsRef.current,
					reconciliationBlocksRef.current,
					instanceId,
				) ||
				Object.keys(tasksRef.current[instanceId] ?? {}).length > 0
			) {
				return false;
			}
			const lifecycleState = getLifecycleState(instance);
			switch (type) {
				case "start":
					return lifecycleState === "stopped" && instance.is_ready;
				case "stop":
					return lifecycleState === "running";
				case "download":
					return lifecycleState === "stopped" && !instance.is_ready;
				case "delete":
					return lifecycleState === "stopped";
				case "preset":
					return lifecycleState === "stopped" && instance.is_admin_instance;
			}
		},
		[findInstanceById],
	);

	const canDeleteLogsFor = useCallback(
		(instanceId: number): boolean => {
			const instance = findInstanceById(instanceId);
			return (
				instance !== undefined &&
				getLifecycleState(instance) === "stopped" &&
				!isInstanceActionBlocked(
					instance,
					tasksRef.current[instanceId] ?? {},
					isInstanceRequestBlocked(
						pendingRequestsRef.current,
						reconciliationBlocksRef.current,
						instanceId,
					),
				)
			);
		},
		[findInstanceById],
	);

	useEffect(() => {
		mountedRef.current = true;
		return () => {
			mountedRef.current = false;
		};
	}, []);

	useEffect(() => {
		localStorage.removeItem("downloadTasks");
		localStorage.removeItem("startTasks");
		localStorage.removeItem("stopTasks");
		localStorage.removeItem("timestamp");
	}, []);

	useEffect(() => {
		operationGenerationRef.current += 1;
		listRequestIdRef.current += 1;
		commitTasks({});
		commitPendingRequests(new Set());
		commitReconciliationBlocks(new Map());
		completedOrRetiredTaskIdsRef.current.clear();
		setUserInstances([]);
		setAdminInstances([]);
		setOtherInstances([]);
		setLoading(true);
	}, [commitPendingRequests, commitReconciliationBlocks, commitTasks, token]);

	useEffect(() => {
		const requestId = ++listRequestIdRef.current;
		const requestToken = token;
		const snapshotOperationGeneration = operationGenerationRef.current;
		let active = true;
		const retryController = createReconciliationRetryController(
			(callback, delay) => window.setTimeout(callback, delay),
			(timer) => window.clearTimeout(timer),
			() => {
				if (
					active &&
					mountedRef.current &&
					tokenRef.current === requestToken &&
					listRequestIdRef.current === requestId &&
					shouldRetryReconciliation(reconciliationBlocksRef.current)
				) {
					requestRefresh();
				}
			},
			RECONCILIATION_RETRY_MS,
		);
		if (!requestToken) {
			setLoading(false);
			return () => {
				active = false;
				retryController.dispose();
			};
		}

		void withReconciliationRetry(
			() =>
				AxiosInstance.get<InstanceListResponse>("instances/").then((response) => {
					const userInstances = response.data.user_instances;
					const adminInstances = response.data.admin_instances;
					if (
						!isInstanceArray(userInstances) ||
						(isAdmin && !isInstanceArray(adminInstances))
					) {
						throw new Error("Invalid instances list response");
					}
					return {
						...response,
						data: {
							...response.data,
							user_instances: userInstances,
							admin_instances: isInstanceArray(adminInstances)
								? adminInstances
								: [],
							other_instances: isInstanceArray(response.data.other_instances)
								? response.data.other_instances
								: [],
						},
					};
				}),
			retryController,
			() =>
				active &&
				mountedRef.current &&
				tokenRef.current === requestToken &&
				listRequestIdRef.current === requestId &&
				shouldRetryReconciliation(reconciliationBlocksRef.current),
		)
			.then((response) => {
				if (
					!active ||
					!mountedRef.current ||
					tokenRef.current !== requestToken
				) {
					return;
				}
				if (
					!shouldAcceptListSnapshot(
						requestId,
						listRequestIdRef.current,
						snapshotOperationGeneration,
						operationGenerationRef.current,
					)
				) {
					if (
						listRequestIdRef.current === requestId &&
						snapshotOperationGeneration !== operationGenerationRef.current
					) {
						requestRefresh();
					}
					return;
				}
				const nextUserInstances = response.data.user_instances;
				const nextAdminInstances = isAdmin ? response.data.admin_instances : [];
				const nextOtherInstances = isAdmin ? response.data.other_instances : [];
				setUserInstances(nextUserInstances);
				setAdminInstances(nextAdminInstances);
				setOtherInstances(nextOtherInstances);
				commitTasks(
					mergeServerTaskBindings(
						tasksRef.current,
						[
							...nextAdminInstances,
							...nextUserInstances,
							...nextOtherInstances,
						],
						completedOrRetiredTaskIdsRef.current,
					),
				);
				commitReconciliationBlocks(
					releaseReconciledBlocks(
						reconciliationBlocksRef.current,
						requestId,
						snapshotOperationGeneration,
					),
				);
				setLoading(false);
			})
			.catch((error: unknown) => {
				if (!active || !mountedRef.current || tokenRef.current !== requestToken) {
					return;
				}
				if (
					!shouldAcceptListSnapshot(
						requestId,
						listRequestIdRef.current,
						snapshotOperationGeneration,
						operationGenerationRef.current,
					)
				) {
					if (
						listRequestIdRef.current === requestId &&
						snapshotOperationGeneration !== operationGenerationRef.current
					) {
						requestRefresh();
					}
					return;
				}
				setLoading(false);
				if (!shouldRetryReconciliation(reconciliationBlocksRef.current)) {
					setAlert("Unable to retrieve system information", "error");
					console.log(getApiErrorMessage(error, "Unable to retrieve instances"));
				}
			});

		return () => {
			active = false;
			retryController.dispose();
		};
	}, [
		commitReconciliationBlocks,
		commitTasks,
		isAdmin,
		refreshVersion,
		requestRefresh,
		setAlert,
		token,
	]);

	const pollTasks = useCallback(async () => {
		if (pollingRef.current || !tokenRef.current || !mountedRef.current) {
			return;
		}
		const requestToken = tokenRef.current;
		const jobs = Object.entries(tasksRef.current).flatMap(([instanceId, instanceTasks]) =>
			TASK_KINDS.flatMap((kind) => {
				const task = instanceTasks[kind];
				return task ? [{ instanceId: Number(instanceId), kind, task }] : [];
			}),
		);
		if (jobs.length === 0) {
			return;
		}
		pollingRef.current = true;
		try {
			await Promise.all(
				jobs.map(async ({ instanceId, kind, task }) => {
					try {
						const response = await AxiosInstance.get<unknown>(
							`instances/task_status/${task.taskId}/`,
						);
						if (
							!mountedRef.current ||
							tokenRef.current !== requestToken
						) {
							return;
						}
						const decoded = decodeTaskStatus(response.data, task);
						const applied = applyPolledTask(
							tasksRef.current,
							instanceId,
							kind,
							task.taskId,
							decoded,
						);
						if (!applied.applied) {
							return;
						}
						commitTasks(applied.ledger);
						if (applied.ended && applied.task) {
							completedOrRetiredTaskIdsRef.current.add(task.taskId);
							requireReconciliation(instanceId);
							if (applied.task.state.toUpperCase() === "SUCCESS") {
								setAlert(TASK_ACTIONS[kind].success, "success");
							} else if (applied.task.state.toUpperCase() === "FAILURE") {
								setAlert(
									`${TASK_ACTIONS[kind].failure} ${applied.task.status}`,
									"error",
								);
							} else {
								setAlert("Operacja została anulowana.", "error");
							}
						}
					} catch (error: unknown) {
						if (
							!mountedRef.current ||
							tokenRef.current !== requestToken
						) {
							return;
						}
						const failed = handleTaskPollError(
							tasksRef.current,
							instanceId,
							kind,
							task.taskId,
							getApiErrorStatus(error),
						);
						if (failed.kind === "retired") {
							completedOrRetiredTaskIdsRef.current.add(task.taskId);
							commitTasks(failed.ledger);
							requireReconciliation(instanceId);
						} else if (failed.kind === "retry") {
							commitTasks(failed.ledger);
						}
					}
				}),
			);
		} finally {
			pollingRef.current = false;
		}
	}, [commitTasks, requireReconciliation, setAlert]);

	useInterval(pollTasks, 5000);

	const handleDeleteLogs = useCallback(
		async (instanceId: number): Promise<boolean> => {
			if (
				!tokenRef.current ||
				!canDeleteLogsFor(instanceId) ||
				!markRequestPending(instanceId, true)
			) {
				return false;
			}
			const requestToken = tokenRef.current;
			operationGenerationRef.current += 1;
			try {
				const response = await AxiosInstance.delete<{ message?: string }>(
					`instances/${instanceId}/logs/delete/`,
				);
				if (
					!mountedRef.current ||
					tokenRef.current !== requestToken
				) {
					return false;
				}
				setAlert(response.data.message ?? "Logi zostały usunięte.", "success");
				return true;
			} catch (error: unknown) {
				if (
					mountedRef.current &&
					tokenRef.current === requestToken
				) {
					setAlert(getApiErrorMessage(error, "Wystąpił błąd."), "error");
				}
				return false;
			} finally {
				if (
					mountedRef.current &&
					tokenRef.current === requestToken
				) {
					markRequestPending(instanceId, false);
					requireReconciliation(instanceId);
				}
			}
		},
		[canDeleteLogsFor, markRequestPending, requireReconciliation, setAlert],
	);

	const handleClick = useCallback(
		async (type: InstanceAction, id: number) => {
			if (type === "logs") {
				setSelectedInstanceId(id);
				return;
			}
			if (type === "preset") {
				if (canStartCardAction(type, id)) {
					setSelectedAdminInstanceId(id);
					setOpenChangePresetModal(true);
				}
				return;
			}
			if (!tokenRef.current || !canStartCardAction(type, id)) {
				return;
			}
			if (!markRequestPending(id, true)) {
				return;
			}
			const requestToken = tokenRef.current;
			operationGenerationRef.current += 1;
			try {
				if (type === "delete") {
					const response = await AxiosInstance.delete<{ message?: string }>(
						`instances/${id}/`,
					);
					if (
						mountedRef.current &&
						tokenRef.current === requestToken
					) {
						setAlert(response.data.message ?? "Instancja została usunięta.", "success");
					}
					return;
				}
				const action = type as TaskKind;
				const response = await AxiosInstance.post<unknown>(
					`instances/${id}/${getTaskActionPath(action)}/`,
				);
				if (
					!mountedRef.current ||
					tokenRef.current !== requestToken
				) {
					return;
				}
				operationGenerationRef.current += 1;
				const taskId = readTaskId(response.data);
				if (!taskId) {
					setAlert("Odpowiedź serwera nie zawiera identyfikatora zadania.", "error");
					return;
				}
				if (completedOrRetiredTaskIdsRef.current.has(taskId)) {
					return;
				}
				const task: TrackedTask = {
					taskId,
					state: "PENDING",
					status: TASK_ACTIONS[action].initialStatus,
				};
				commitTasks(setTrackedTask(tasksRef.current, id, action, task));
				void pollTasks();
			} catch (error: unknown) {
				if (
					mountedRef.current &&
					tokenRef.current === requestToken
				) {
					setAlert(getApiErrorMessage(error, "Wystąpił błąd."), "error");
				}
			} finally {
				if (
					mountedRef.current &&
					tokenRef.current === requestToken
				) {
					markRequestPending(id, false);
					requireReconciliation(id);
				}
			}
		},
		[
			canStartCardAction,
			commitTasks,
			markRequestPending,
			pollTasks,
			requireReconciliation,
			setAlert,
		],
	);

	const handlePresetModalClose = useCallback(() => {
		setOpenChangePresetModal(false);
		setSelectedAdminInstanceId(null);
		requestRefresh();
	}, [requestRefresh]);

	const handleCreateModalClose = useCallback(() => {
		setOpenCreateInstanceModal(false);
		requestRefresh();
	}, [requestRefresh]);

	const handleClickFAB = () => {
		setOpenCreateInstanceModal(true);
	};

	return (
		<>
			<Stack spacing={2} alignItems="center">
				{isAdmin && (
					<Box sx={{ width: "100%" }}>
						<Divider sx={{ marginY: 2 }} />
						<Typography align="center" fontWeight={700} fontSize={24}>
							Instancje Główne
						</Typography>
						<Divider sx={{ marginY: 2 }} />
						{loading ? (
							<Skeleton variant="rectangular" height={200} />
						) : (
							<AdminInstances
								instances={adminInstances}
								tasks={tasks}
								pendingRequests={pendingRequests}
								reconciliationBlocks={reconciliationBlocks}
								handleClick={handleClick}
							/>
						)}
					</Box>
				)}
				<Box sx={{ width: "100%" }}>
					<Divider sx={{ marginY: 2 }} />
					<Typography align="center" fontWeight={700} fontSize={24}>
						Instancje Użytkownika
					</Typography>
					<Divider sx={{ marginY: 2 }} />
					{loading ? (
						<Skeleton variant="rectangular" height={200} />
					) : (
						<UserInstances
							instances={userInstances}
							tasks={tasks}
							pendingRequests={pendingRequests}
							reconciliationBlocks={reconciliationBlocks}
							emptyMessage="Brak instancji użytkownika"
							handleClick={handleClick}
						/>
					)}
				</Box>
				{isAdmin && (
					<Box sx={{ width: "100%" }}>
						<Divider sx={{ marginY: 2 }} />
						<Typography align="center" fontWeight={700} fontSize={24}>
							Inne instancje
						</Typography>
						<Divider sx={{ marginY: 2 }} />
						{loading ? (
							<Skeleton variant="rectangular" height={200} />
						) : (
							<UserInstances
								instances={otherInstances}
								tasks={tasks}
								pendingRequests={pendingRequests}
								reconciliationBlocks={reconciliationBlocks}
								showOwner
								emptyMessage="Brak innych instancji"
								handleClick={handleClick}
							/>
						)}
					</Box>
				)}
			</Stack>
			{openCreateInstanceModal && (
				<CreateInstanceModal
					open={openCreateInstanceModal}
					onClose={handleCreateModalClose}
				/>
			)}
			{openChangePresetModal && selectedAdminInstanceId !== null && (
				<ChangePresetModal
					open={openChangePresetModal}
					onClose={handlePresetModalClose}
					selectedAdminInstanceId={selectedAdminInstanceId}
				/>
			)}
			{selectedInstanceId !== null && (
				<ServerLogsModal
					selectedInstanceId={selectedInstanceId}
					onClose={() => setSelectedInstanceId(null)}
					canDeleteLogs={canDeleteLogsFor(selectedInstanceId)}
					onDeleteLogs={handleDeleteLogs}
				/>
			)}
			<FAB handleClick={handleClickFAB} />
		</>
	);
}
