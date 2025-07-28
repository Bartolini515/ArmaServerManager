import {
	Box,
	Button,
	Card,
	CardActions,
	CardContent,
	Typography,
} from "@mui/material";
import MyButton from "../../UI/forms/MyButton";
import DeleteForeverIcon from "@mui/icons-material/DeleteForever";
import TextSnippetIcon from "@mui/icons-material/TextSnippet";
import FileUploadIcon from "@mui/icons-material/FileUpload";

interface Props {
	id: number;
	title: string;
	port: number;
	log_file: string | null;
	preset: string;
	is_running: boolean;
	is_ready: boolean;
	is_admin_instance: boolean;
	handleClick: (
		type: "start" | "stop" | "download" | "delete" | "logs" | "preset",
		id: number
	) => void;
	downloadTask?: {
		taskId: number;
		state: string;
		status: string;
	};
	startTask?: {
		taskId: number;
		state: string;
		status: string;
	};
	stopTask?: {
		taskId: number;
		state: string;
		status: string;
	};
	timestamp?: number | null;
}

export default function InstanceCard(props: Props) {
	const endTime = props.timestamp
		? new Date(props.timestamp + 3600000).getTime()
		: null;
	return (
		<Card sx={{ minWidth: { xs: 300, sm: 400, md: 800 } }}>
			<CardContent sx={{ padding: "8px 16px" }}>
				<Box
					sx={{
						display: "flex",
						justifyContent: "space-between",
						marginBottom: "16px",
					}}
				>
					{!props.is_admin_instance && (
						<Button
							sx={{
								minWidth: 0,
								padding: "6px",
							}}
							onClick={() => props.handleClick("delete", props.id)}
						>
							<DeleteForeverIcon sx={{ color: "red" }} fontSize="medium" />
						</Button>
					)}
					<Typography
						variant="h5"
						component="div"
						sx={{
							display: "flex",
							justifyContent: "center",
							fontSize: 28,
						}}
					>
						{props.title}
					</Typography>
					<Box></Box>
				</Box>
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
					{endTime && props.is_running && (
						<Typography component={"li"} fontWeight={"bold"}>
							Godzina wyłączenia:{" "}
							<Typography component="span">
								{new Date(endTime).toLocaleTimeString("pl-PL", {
									hour: "2-digit",
									minute: "2-digit",
								})}
							</Typography>
						</Typography>
					)}
				</Typography>
			</CardContent>
			<CardActions sx={{ justifyContent: "right" }}>
				{props.is_admin_instance && !props.is_running && (
					<Button
						sx={{
							minWidth: 0,
							padding: "6px",
						}}
						onClick={() => props.handleClick("preset", props.id)}
					>
						<FileUploadIcon sx={{ color: "gray" }} fontSize="medium" />
					</Button>
				)}
				{props.log_file && (
					<Button
						sx={{
							minWidth: 0,
							padding: "6px",
						}}
						onClick={() => props.handleClick("logs", props.id)}
					>
						<TextSnippetIcon sx={{ color: "gray" }} fontSize="medium" />
					</Button>
				)}

				{props.is_ready ? (
					props.is_running ? (
						props.stopTask && props.stopTask.state === "PROGRESS" ? (
							<Typography variant="body2" color="textSecondary">
								{props.stopTask.status}
							</Typography>
						) : (
							<MyButton
								label={"Zatrzymaj"}
								type={"button"}
								color={"error"}
								onClick={() => props.handleClick("stop", props.id)}
							/>
						)
					) : props.startTask && props.startTask.state === "PROGRESS" ? (
						<Typography variant="body2" color="textSecondary">
							{props.startTask.status}
						</Typography>
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
