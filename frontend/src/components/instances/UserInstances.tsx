import { useEffect, useState } from "react";
import { useAlert } from "../../contexts/AlertContext";
import { useInterval } from "../../hooks/use-interval";
import AxiosInstance from "../AxiosInstance";
import InstanceCard from "./InstanceCard";
import { Stack, Typography } from "@mui/material";

interface Instance {
	id: number;
	name: string;
	user: string;
	preset: string;
	start_file_path: string;
	created_at: string;
	is_admin_instance: boolean;
	is_ready: boolean;
	is_running: boolean;
	port: number;
}

interface DownloadTask {
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
		[instanceId: number]: DownloadTask;
	}>(() => {
		const savedTasks = localStorage.getItem("downloadTasks");
		return savedTasks ? JSON.parse(savedTasks) : {};
	});

	useEffect(() => {
		localStorage.setItem("downloadTasks", JSON.stringify(downloadTasks));
	}, [downloadTasks]);

	const GetTasksStatus = () => {
		Object.entries(downloadTasks).forEach(([instanceId, task]) => {
			if (task.state === "SUCCESS" || task.state === "FAILURE") {
				return; // Skip completed tasks
			}

			AxiosInstance.get(`instances/task_status/${task.taskId}/`)
				.then((response) => {
					const state = response.data.state;
					const status = response.data.result.status;

					console.log(response.data);

					if (state !== task.state || status !== task.status) {
						setDownloadTasks((prev) => ({
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
						setAlert("Pobieranie zakończone pomyślnie!", "success");
					} else if (state === "FAILURE") {
						setAlert(`Pobieranie nie powiodło się: ${status}`, "error");
					}
				})
				.catch((error: any) => {
					console.log(error);
					setAlert(error.message, "error");
					setDownloadTasks((prev) => ({
						...prev,
						[Number(instanceId)]: {
							...prev[Number(instanceId)],
							state: "FAILURE",
							status: `Wystąpił błąd: ${error.message}`,
						},
					}));
				});
		});
	};

	useInterval(GetTasksStatus, 5000);

	const StartDownload = (id: number) => {
		AxiosInstance.post(`instances/${id}/download_mods/`)
			.then((response) => {
				setDownloadTasks((prev) => ({
					...prev,
					[id]: {
						taskId: response.data.task_id,
						state: "PROGRESS",
						status: "Download started",
					},
				}));
			})
			.catch((error: any) => {
				console.log(error);
				setAlert(error.message, "error");
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
				setAlert(error.message, "error");
			});
	};

	const handleClick = (
		type: "start" | "stop" | "download" | "delete",
		id: number
	) => {
		switch (type) {
			case "start":
				// Start the instance
				break;
			case "stop":
				// Stop the instance
				break;
			case "download":
				StartDownload(id);
				break;
			case "delete":
				DeleteInstance(id);
				break;
		}
	};
	return (
		<Stack spacing={2} alignItems="center" width="100%">
			{props.userInstances.length > 0 ? (
				props.userInstances.map((instance) => (
					<InstanceCard
						key={instance.id}
						id={instance.id}
						title={instance.name}
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
					/>
				))
			) : (
				<Typography variant="body1" color="textSecondary" fontSize={20}>
					Brak instancji użytkownika.
				</Typography>
			)}
		</Stack>
	);
}
