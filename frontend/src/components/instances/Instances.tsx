import { Divider, Skeleton, Stack, Typography } from "@mui/material";
import Box from "@mui/material/Box";
import UserInstances from "./UserInstances";
import AdminInstances from "./AdminInstances";
import AxiosInstance from "../AxiosInstance";
import { useEffect, useState } from "react";
import { useAuth } from "../../contexts/AuthContext";
import { useAlert } from "../../contexts/AlertContext";
import FAB from "../../UI/forms/FAB";
import CreateInstanceModal from "./modals/CreateInstanceModal";
import { useInterval } from "../../hooks/use-interval";

interface Instance {
	id: number;
	name: string;
	user: string;
	preset: string;
	log_file: string | null;
	start_file_path: string;
	port: number;
	pid: number;
	is_ready: boolean;
	is_running: boolean;
	is_admin_instance: boolean;
	created_at: string;
}

interface TaskTemplate {
	taskId: number;
	state: string;
	status: string;
}

export default function Instances() {
	const [openCreateInstanceModal, setOpenCreateInstanceModal] = useState(false);
	const [userInstances, setUserInstances] = useState<Instance[]>([]);
	const [adminInstances, setAdminInstances] = useState<Instance[]>([]);
	const [refresh, setRefresh] = useState(false);
	const [selectedInstanceId, setSelectedInstanceId] = useState<number | null>(
		null
	);

	const { isAdmin } = useAuth();
	const { setAlert } = useAlert();
	const [loading, setLoading] = useState(true);

	const [downloadTasks, setDownloadTasks] = useState<{
		[instanceId: number]: TaskTemplate;
	}>(() => {
		const savedTasks = localStorage.getItem("downloadTasks");
		return savedTasks ? JSON.parse(savedTasks) : {};
	});
	const [startTasks, setStartTasks] = useState<{
		[instanceId: number]: TaskTemplate;
	}>(() => {
		const savedTasks = localStorage.getItem("startTasks");
		return savedTasks ? JSON.parse(savedTasks) : {};
	});
	const [stopTasks, setStopTasks] = useState<{
		[instanceId: number]: TaskTemplate;
	}>(() => {
		const savedTasks = localStorage.getItem("stopTasks");
		return savedTasks ? JSON.parse(savedTasks) : {};
	});
	useEffect(() => {
		localStorage.setItem("downloadTasks", JSON.stringify(downloadTasks));
		localStorage.setItem("startTasks", JSON.stringify(startTasks));
		localStorage.setItem("stopTasks", JSON.stringify(stopTasks));
	}, [downloadTasks, startTasks, stopTasks]);

	const tasksMap = {
		download: {
			dict: downloadTasks,
			setDict: setDownloadTasks,
			alerts_messages: {
				success: "Pobieranie zakończone pomyślnie!",
				failure: "Pobieranie nie powiodło się: ",
			},
		},
		start: {
			dict: startTasks,
			setDict: setStartTasks,
			alerts_messages: {
				success: "Instancja została uruchomiona pomyślnie!",
				failure: "Uruchomienie instancji nie powiodło się: ",
			},
		},
		stop: {
			dict: stopTasks,
			setDict: setStopTasks,
			alerts_messages: {
				success: "Instancja została zatrzymana pomyślnie!",
				failure: "Zatrzymanie instancji nie powiodło się: ",
			},
		},
	};

	const GetTasksStatus = () => {
		for (const key in tasksMap) {
			const { dict, setDict, alerts_messages } =
				tasksMap[key as keyof typeof tasksMap];
			Object.entries(dict).forEach(([instanceId, task]) => {
				if (task.state === "SUCCESS" || task.state === "FAILURE") {
					return; // Skip completed tasks
				}

				AxiosInstance.get(`instances/task_status/${task.taskId}/`)
					.then((response) => {
						const state = response.data.state;
						const status = response.data.result.status;

						if (state !== task.state || status !== task.status) {
							setDict((prev) => ({
								...prev,
								[Number(instanceId)]: {
									...prev[Number(instanceId)],
									state: state,
									status: status,
								},
							}));
						}

						if (state === "SUCCESS") {
							setRefresh(true);
							setAlert(alerts_messages.success, "success");
						} else if (state === "FAILURE") {
							setRefresh(true);
							setAlert(`${alerts_messages.failure} ${status}`, "error");
						}
					})
					.catch((error: any) => {
						console.log(error);
						setAlert(
							error.response.data.message
								? error.response.data.message
								: error.message,
							"error"
						);
						setDict((prev) => ({
							...prev,
							[Number(instanceId)]: {
								...prev[Number(instanceId)],
								state: "FAILURE",
								status: `Wystąpił błąd: ${error.message}`,
							},
						}));
					});
			});
		}
	};

	useInterval(GetTasksStatus, 5000);

	const GetData = () => {
		AxiosInstance.get("instances/")
			.then((response) => {
				setUserInstances(response.data.user_instances);

				if (isAdmin) {
					setAdminInstances(response.data.admin_instances);
				}
				setLoading(false);
			})
			.catch((error: any) => {
				console.log(error);
				setAlert("Unable to retrieve system information", "error");
			});
	};

	useEffect(() => {
		GetData();
		setRefresh(false);
	}, [refresh]);

	const handleClickFAB = () => {
		setOpenCreateInstanceModal(true);
	};

	return (
		<>
			<Stack spacing={2} alignItems="center">
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
							userInstances={userInstances}
							setRefresh={setRefresh}
							startTasks={startTasks}
							setStartTasks={setStartTasks}
							stopTasks={stopTasks}
							setStopTasks={setStopTasks}
							downloadTasks={downloadTasks}
							setDownloadTasks={setDownloadTasks}
							selectedInstanceId={selectedInstanceId}
							setSelectedInstanceId={setSelectedInstanceId}
						/>
					)}
				</Box>
				{isAdmin ? (
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
								adminInstances={adminInstances}
								setRefresh={setRefresh}
								startTasks={startTasks}
								setStartTasks={setStartTasks}
								stopTasks={stopTasks}
								setStopTasks={setStopTasks}
								downloadTasks={downloadTasks}
								setDownloadTasks={setDownloadTasks}
								selectedInstanceId={selectedInstanceId}
								setSelectedInstanceId={setSelectedInstanceId}
							/>
						)}
					</Box>
				) : null}
			</Stack>
			{openCreateInstanceModal && (
				<CreateInstanceModal
					open={openCreateInstanceModal}
					onClose={() => {
						setOpenCreateInstanceModal(false), setRefresh(true);
					}}
				/>
			)}
			<FAB handleClick={handleClickFAB} />
		</>
	);
}
