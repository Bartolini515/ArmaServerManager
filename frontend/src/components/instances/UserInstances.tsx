import { useEffect, useState } from "react";
import { useAlert } from "../../contexts/AlertContext";
import { useInterval } from "../../hooks/use-interval";
import AxiosInstance from "../AxiosInstance";
import InstanceCard from "./InstanceCard";
import { Stack, Typography } from "@mui/material";
import ServerLogsModal from "./modals/ServerLogsModal";

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

interface Props {
	userInstances: Instance[];
	setRefresh: (refresh: boolean) => void;
}

export default function UserInstances(props: Props) {
	const { setAlert } = useAlert();
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
	const [selectedInstanceId, setSelectedInstanceId] = useState<number | null>(
		null
	);
	const [timestamp, setTimestamp] = useState<number | null>(() => {
		const savedTimestamp = localStorage.getItem("timestamp");
		return savedTimestamp ? JSON.parse(savedTimestamp) : null;
	});

	useEffect(() => {
		localStorage.setItem("timestamp", JSON.stringify(timestamp));
	}, [timestamp]);

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
							props.setRefresh(true);
							setAlert(alerts_messages.success, "success");
						} else if (state === "FAILURE") {
							props.setRefresh(true);
							setAlert(`${alerts_messages.failure} ${status}`, "error");
						}
					})
					.catch((error: any) => {
						console.log(error);
						setAlert(error.response.data.message || error.message, "error");
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

	const StartInstance = (id: number) => {
		AxiosInstance.post(`instances/${id}/start/`)
			.then((response) => {
				setTimestamp(Date.now());
				setStartTasks((prev) => ({
					...prev,
					[id]: {
						taskId: response.data.task_id,
						state: "PROGRESS",
						status: "Rozpoczęto uruchamianie",
					},
				}));
			})
			.catch((error: any) => {
				console.log(error);
				setAlert(error.response.data.message || error.message, "error");
			});
	};

	const StopInstance = (id: number) => {
		AxiosInstance.post(`instances/${id}/stop/`)
			.then((response) => {
				setTimestamp(null);
				setStopTasks((prev) => ({
					...prev,
					[id]: {
						taskId: response.data.task_id,
						state: "PROGRESS",
						status: "Rozpoczęto zatrzymywanie",
					},
				}));
			})
			.catch((error: any) => {
				console.log(error);
				setAlert(error.response.data.message || error.message, "error");
			});
	};

	const StartDownload = (id: number) => {
		AxiosInstance.post(`instances/${id}/download_mods/`)
			.then((response) => {
				setDownloadTasks((prev) => ({
					...prev,
					[id]: {
						taskId: response.data.task_id,
						state: "PROGRESS",
						status: "Rozpoczęto pobieranie",
					},
				}));
			})
			.catch((error: any) => {
				console.log(error);
				setAlert(error.response.data.message || error.message, "error");
			});
	};

	const DeleteInstance = (id: number) => {
		AxiosInstance.delete(`instances/${id}/`)
			.then((response) => {
				setAlert(response.data.message, "success");
				props.setRefresh(true);
			})
			.catch((error: any) => {
				console.log(error);
				setAlert(error.response.data.message || error.message, "error");
			});
	};

	const handleClick = (
		type: "start" | "stop" | "download" | "delete" | "logs",
		id: number
	) => {
		switch (type) {
			case "start":
				StartInstance(id);
				break;
			case "stop":
				StopInstance(id);
				break;
			case "download":
				StartDownload(id);
				break;
			case "delete":
				DeleteInstance(id);
				break;
			case "logs":
				setSelectedInstanceId(id);
				break;
		}
	};
	return (
		<>
			<Stack spacing={2} alignItems="center" width="100%">
				{props.userInstances.length > 0 ? (
					props.userInstances.map((instance) => (
						<InstanceCard
							key={instance.id}
							id={instance.id}
							title={instance.name}
							log_file={instance.log_file}
							port={instance.port}
							preset={instance.preset}
							is_running={instance.is_running}
							is_ready={instance.is_ready}
							handleClick={handleClick}
							downloadTask={
								downloadTasks[instance.id]
									? downloadTasks[instance.id]
									: undefined
							}
							startTask={
								startTasks[instance.id] ? startTasks[instance.id] : undefined
							}
							stopTask={
								stopTasks[instance.id] ? stopTasks[instance.id] : undefined
							}
							timestamp={timestamp}
						/>
					))
				) : (
					<Typography variant="body1" color="textSecondary" fontSize={20}>
						Brak instancji użytkownika
					</Typography>
				)}
			</Stack>
			{selectedInstanceId !== null && (
				<ServerLogsModal
					selectedInstanceId={selectedInstanceId}
					onClose={() => setSelectedInstanceId(null)}
				/>
			)}
		</>
	);
}
