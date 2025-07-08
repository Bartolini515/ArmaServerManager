import Box from "@mui/material/Box";
import AxiosInstance from "../AxiosInstance";
import { useEffect, useState } from "react";
import { useAlert } from "../../contexts/AlertContext";
import { useAuth } from "../../contexts/AuthContext";
import { Skeleton } from "@mui/material";

interface Task {
	id: number;
	task_name: string;
	description: string | null;
	user: string | null;
	completion_status: boolean;
	due_date: string | null;
	event: string | null;
	event_id: number | null;
	user_id: number | null;
	color: string;
}

export default function Dashboard() {
	const { setAlert } = useAlert();
	const { user } = useAuth();
	const [loading, setLoading] = useState(true);

	const GetData = () => {
		// AxiosInstance.get("timetable/")
		// 	.then((response) => {
		// 		setEvents(response.data);
		// 	})
		// 	.catch((error: any) => {
		// 		console.log(error);
		// 		setAlert(error.message, "error");
		// 	});
		// AxiosInstance.get("tasks/")
		// 	.then((response) => {
		// 		let tempTasks: Task[] = [];
		// 		response.data.forEach((task: Task) => {
		// 			if (
		// 				((task.due_date &&
		// 					task.due_date >= new Date().toISOString().split("T")[0] &&
		// 					task.due_date <=
		// 						new Date(new Date().setDate(new Date().getDate() + 7))
		// 							.toISOString()
		// 							.split("T")[0]) ||
		// 					(task.due_date === null && task.completion_status === false)) &&
		// 				task.user_id === user?.id &&
		// 				task.completion_status === false
		// 			) {
		// 				tempTasks.push(task);
		// 			}
		// 		});
		// 		tempTasks.sort((a, b) => {
		// 			if (a.due_date && b.due_date) {
		// 				return a.due_date.localeCompare(b.due_date);
		// 			}
		// 			return 0;
		// 		});
		// 		tempTasks = tempTasks.slice(0, 5);
		// 		setTasks(tempTasks);
		// 		setLoading(false);
		// 	})
		// 	.catch((error: any) => {
		// 		console.log(error);
		// 		setAlert(error.message, "error");
		// 	});
	};

	useEffect(() => {
		GetData();
	}, [user]);

	return (
		<Box
			sx={{
				flexGrow: { sm: 1, xs: "none" },
				padding: 2,
				alignContent: "center",
			}}
		>
			<Box
				sx={{
					display: "flex",
					gap: 2,
					flexDirection: { sm: "row", xs: "column" },
					marginBottom: 2,
				}}
			>
				<Box
					sx={{
						flex: { sm: 1, xs: "none" },
						height: "100%",
						minHeight: "auto",
						boxShadow: 3,
						padding: "20px",
					}}
				>
					{loading ? (
						<Skeleton variant="rectangular" height={200} />
					) : (
						"Placeholder" // Placeholder
						// TODO: find a use for this
					)}
				</Box>
				<Box
					sx={{
						flex: { sm: 1, xs: "none" },
						height: "100%",
						boxShadow: 0, // TODO: 3, find a use for this
						padding: "20px",
					}}
				></Box>
			</Box>
			<Box
				sx={{
					maxheight: { sm: 350, xs: 300 },
					minHeight: "auto",
					boxShadow: 3,
					padding: "20px",
				}}
			>
				{loading ? (
					<Skeleton variant="rectangular" height={300} />
				) : (
					"Placeholder" // Placeholder
					// TODO: find a use for this
				)}
			</Box>
		</Box>
	);
}
