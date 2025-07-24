import { Card, CardActions, CardContent, Typography } from "@mui/material";
import MyButton from "../../UI/forms/MyButton";

interface Props {
	id: number;
	title: string;
	port: number;
	preset: string;
	is_running: boolean;
	is_ready: boolean;
	handleClick: (type: "start" | "stop" | "download", id: number) => void;
	downloadTask?: {
		taskId: number;
		state: string;
		status: string;
	};
}

export default function InstanceCard(props: Props) {
	return (
		<Card sx={{ minWidth: { xs: 300, sm: 400, md: 800 } }}>
			<CardContent sx={{ padding: "8px 16px" }}>
				<Typography
					variant="h5"
					component="div"
					sx={{
						display: "flex",
						justifyContent: "center",
						fontSize: 28,
						marginBottom: "16px",
					}}
				>
					{props.title}
				</Typography>

				<Typography component={"ul"}>
					<Typography component={"li"} fontWeight={"bold"}>
						Preset:{" "}
						<Typography component="span">
							{props.preset.split("/").pop()}
						</Typography>
					</Typography>
					<Typography component={"li"} fontWeight={"bold"}>
						Port: <Typography component="span">{props.port}</Typography>
					</Typography>
				</Typography>
			</CardContent>
			<CardActions sx={{ justifyContent: "right" }}>
				{props.is_ready ? (
					props.is_running ? (
						<MyButton
							label={"Zatrzymaj"}
							type={"button"}
							color={"error"}
							onClick={() => props.handleClick("stop", props.id)}
						/>
					) : (
						<MyButton
							label={"Uruchom"}
							type={"button"}
							color="success"
							onClick={() => props.handleClick("start", props.id)}
						/>
					)
				) : props.downloadTask && props.downloadTask.state === "PROGRESS" ? (
					<Typography variant="body2" color="textSecondary">
						{props.downloadTask.status}
					</Typography>
				) : (
					<MyButton
						label={"Pobierz mody"}
						type={"button"}
						onClick={() => props.handleClick("download", props.id)}
					/>
				)}
			</CardActions>
		</Card>
	);
}
