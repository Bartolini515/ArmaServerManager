import {
	Box,
	Button,
	Card,
	CardActions,
	CardContent,
	Typography,
} from "@mui/material";
import DeleteForeverIcon from "@mui/icons-material/DeleteForever";
import FileUploadIcon from "@mui/icons-material/FileUpload";
import TextSnippetIcon from "@mui/icons-material/TextSnippet";
import {
	getLifecycleState,
	getOwnerName,
	getShutdownAt,
	isInstanceActionBlocked,
} from "./taskState";
import { TASK_KINDS, type Instance, type InstanceAction, type InstanceTaskSet } from "./types";

interface Props {
	instance: Instance;
	activeTasks: InstanceTaskSet;
	requestPending: boolean;
	reconciling: boolean;
	showOwner?: boolean;
	allowPreset?: boolean;
	handleClick: (type: InstanceAction, id: number) => void;
}

export default function InstanceCard(props: Props) {
	const { instance } = props;
	const lifecycleState = getLifecycleState(instance);
	const actionBlocked = isInstanceActionBlocked(
		instance,
		props.activeTasks,
		props.requestPending || props.reconciling,
	);
	const activeTaskKind = TASK_KINDS.find(
		(kind) => props.activeTasks[kind] !== undefined,
	);
	const activeTask = activeTaskKind
		? props.activeTasks[activeTaskKind]
		: undefined;
	const shutdownAt = getShutdownAt(instance);
	const destructiveDisabled = actionBlocked || lifecycleState !== "stopped";
	const primaryStatus =
		activeTask?.status ??
		(props.requestPending
			? "Przetwarzanie żądania..."
			: props.reconciling
				? "Aktualizowanie stanu..."
				: null);
	const primaryAction =
		activeTaskKind ??
		(lifecycleState === "running"
			? "stop"
			: lifecycleState === "stopped"
				? instance.is_ready
					? "start"
					: "download"
				: null);
	const primaryActionLabels = {
		start: "Uruchom",
		stop: "Zatrzymaj",
		download: "Pobierz mody",
	};
	const primaryActionColors = {
		start: "success",
		stop: "error",
		download: "primary",
	} as const;

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
					{!instance.is_admin_instance ? (
						<Button
							aria-label={`Usuń instancję ${instance.name}`}
							sx={{ minWidth: 0, padding: "6px" }}
							disabled={destructiveDisabled}
							onClick={() => props.handleClick("delete", instance.id)}
						>
							<DeleteForeverIcon sx={{ color: "red" }} fontSize="medium" />
						</Button>
					) : (
						<Box sx={{ width: 36 }} />
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
						{instance.name}
					</Typography>
					<Box sx={{ width: 36 }} />
				</Box>
				<Typography component="ul">
					<Typography component="li" fontWeight="bold">
						Preset: <Typography component="span">{instance.preset.split("/").pop()}</Typography>
					</Typography>
					<Typography component="li" fontWeight="bold">
						Port: <Typography component="span">{instance.port}</Typography>
					</Typography>
					{props.showOwner && (
						<Typography component="li" fontWeight="bold">
							Użytkownik: <Typography component="span">{getOwnerName(instance)}</Typography>
						</Typography>
					)}
					{shutdownAt && (
						<Typography component="li" fontWeight="bold">
							Godzina wyłączenia:{" "}
							<Typography component="span">
								{new Date(shutdownAt).toLocaleTimeString("pl-PL", {
									hour: "2-digit",
									minute: "2-digit",
								})}
							</Typography>
						</Typography>
					)}
				</Typography>
			</CardContent>
			<CardActions sx={{ justifyContent: "right" }}>
				{props.allowPreset && instance.is_admin_instance && lifecycleState === "stopped" && (
					<Button
						aria-label={`Zmień preset instancji ${instance.name}`}
						sx={{ minWidth: 0, padding: "6px" }}
						disabled={actionBlocked}
						onClick={() => props.handleClick("preset", instance.id)}
					>
						<FileUploadIcon sx={{ color: "gray" }} fontSize="medium" />
					</Button>
				)}
				{instance.log_file && (
					<Button
						aria-label={`Wyświetl logi instancji ${instance.name}`}
						sx={{ minWidth: 0, padding: "6px" }}
						onClick={() => props.handleClick("logs", instance.id)}
					>
						<TextSnippetIcon sx={{ color: "gray" }} fontSize="medium" />
					</Button>
				)}
				{primaryStatus ? (
					<>
						<Typography role="status" variant="body2" color="textSecondary">
							{primaryStatus}
						</Typography>
						{primaryAction && (
							<Button
								variant="contained"
								color={primaryActionColors[primaryAction]}
								disabled
							>
								{primaryActionLabels[primaryAction]}
							</Button>
						)}
					</>
				) : lifecycleState === "starting" ? (
					<Typography role="status" variant="body2" color="textSecondary">
						Uruchamianie instancji...
					</Typography>
				) : lifecycleState === "stopping" ? (
					<Typography role="status" variant="body2" color="textSecondary">
						Zatrzymywanie instancji...
					</Typography>
				) : lifecycleState === "unknown" ? (
					<Typography role="status" variant="body2" color="textSecondary">
						Stan instancji jest nieznany
					</Typography>
				) : lifecycleState === "running" ? (
					<Button
						variant="contained"
						color={primaryActionColors.stop}
						disabled={actionBlocked}
						onClick={() => props.handleClick("stop", instance.id)}
					>
						{primaryActionLabels.stop}
					</Button>
				) : instance.is_ready ? (
					<Button
						variant="contained"
						color={primaryActionColors.start}
						disabled={actionBlocked}
						onClick={() => props.handleClick("start", instance.id)}
					>
						{primaryActionLabels.start}
					</Button>
				) : (
					<Button
						variant="contained"
						color={primaryActionColors.download}
						disabled={actionBlocked}
						onClick={() => props.handleClick("download", instance.id)}
					>
						{primaryActionLabels.download}
					</Button>
				)}
			</CardActions>
		</Card>
	);
}
