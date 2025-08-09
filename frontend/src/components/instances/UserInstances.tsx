import { useEffect, useState } from "react";
import { useAlert } from "../../contexts/AlertContext";
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
	startTasks: {
		[instanceId: number]: TaskTemplate;
	};
	setStartTasks: (
		tasks:
			| { [instanceId: number]: TaskTemplate }
			| ((prev: { [instanceId: number]: TaskTemplate }) => {
					[instanceId: number]: TaskTemplate;
			  })
	) => void;
	stopTasks: {
		[instanceId: number]: TaskTemplate;
	};
	setStopTasks: (
		tasks:
			| { [instanceId: number]: TaskTemplate }
			| ((prev: { [instanceId: number]: TaskTemplate }) => {
					[instanceId: number]: TaskTemplate;
			  })
	) => void;
	downloadTasks: { [instanceId: number]: TaskTemplate };
	setDownloadTasks: (
		tasks:
			| { [instanceId: number]: TaskTemplate }
			| ((prev: { [instanceId: number]: TaskTemplate }) => {
					[instanceId: number]: TaskTemplate;
			  })
	) => void;
	selectedInstanceId: number | null;
	setSelectedInstanceId: (id: number | null) => void;
}

export default function UserInstances(props: Props) {
	const { setAlert } = useAlert();
	const [timestamp, setTimestamp] = useState<number | null>(() => {
		const savedTimestamp = localStorage.getItem("timestamp");
		return savedTimestamp ? JSON.parse(savedTimestamp) : null;
	});

	useEffect(() => {
		localStorage.setItem("timestamp", JSON.stringify(timestamp));
	}, [timestamp]);

	const StartInstance = (id: number) => {
		AxiosInstance.post(`instances/${id}/start/`)
			.then((response) => {
				setTimestamp(Date.now());
				props.setStartTasks((prev) => ({
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
				setAlert(
					error.response?.data?.message
						? error.response.data.message
						: error.message,
					"error"
				);
			});
	};

	const StopInstance = (id: number) => {
		AxiosInstance.post(`instances/${id}/stop/`)
			.then((response) => {
				setTimestamp(null);
				props.setStopTasks((prev) => ({
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
				setAlert(
					error.response?.data?.message
						? error.response.data.message
						: error.message,
					"error"
				);
			});
	};

	const StartDownload = (id: number) => {
		AxiosInstance.post(`instances/${id}/download_mods/`)
			.then((response) => {
				props.setDownloadTasks((prev) => ({
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
				setAlert(
					error.response?.data?.message
						? error.response.data.message
						: error.message,
					"error"
				);
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
				setAlert(
					error.response?.data?.message
						? error.response.data.message
						: error.message,
					"error"
				);
			});
	};

	const handleClick = (
		type: "start" | "stop" | "download" | "delete" | "logs" | "preset",
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
				props.setSelectedInstanceId(id);
				break;
			case "preset":
				// Preset logic is only for admin instances
				setAlert(
					"Ta funkcja jest dostępna tylko dla instancji administratora.",
					"error"
				);
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
							is_admin_instance={instance.is_admin_instance}
							handleClick={handleClick}
							downloadTask={
								props.downloadTasks[instance.id]
									? props.downloadTasks[instance.id]
									: undefined
							}
							startTask={
								props.startTasks[instance.id]
									? props.startTasks[instance.id]
									: undefined
							}
							stopTask={
								props.stopTasks[instance.id]
									? props.stopTasks[instance.id]
									: undefined
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
			{props.selectedInstanceId !== null && (
				<ServerLogsModal
					selectedInstanceId={props.selectedInstanceId}
					onClose={() => props.setSelectedInstanceId(null)}
				/>
			)}
		</>
	);
}
