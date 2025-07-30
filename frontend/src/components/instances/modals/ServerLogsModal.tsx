import { useEffect, useRef, useState } from "react";
import {
	Backdrop,
	Box,
	Button,
	Fade,
	Modal,
	TextField,
	Typography,
} from "@mui/material";
import FileDownloadIcon from "@mui/icons-material/FileDownload";
import RefreshIcon from "@mui/icons-material/Refresh";
import CloseIcon from "@mui/icons-material/Close";
import AxiosInstance from "../../AxiosInstance";
import { useAlert } from "../../../contexts/AlertContext";

const style = {
	position: "absolute",
	top: "50%",
	left: "50%",
	transform: "translate(-50%, -50%)",
	width: { xs: "80%", sm: "800px" },
	height: "90%",
	maxHeight: "max-content",
	overflow: "auto",
	bgcolor: "background.paper",
	border: "2px solid #000",
	borderRadius: 4,
	boxShadow: 24,
	p: 4,
};

type Props = {
	selectedInstanceId: number;
	onClose: () => void;
};

export default function ServerLogsModal(props: Props) {
	const { setAlert } = useAlert();
	const [logs, setLogs] = useState("");
	const logTextArea = useRef<HTMLTextAreaElement>(null);

	useEffect(() => {
		if (props.selectedInstanceId !== null) {
			GetLogs(props.selectedInstanceId);
		}
	}, [props.selectedInstanceId]);

	const GetLogs = (id: number) => {
		AxiosInstance.get(`instances/${id}/logs/`)
			.then((response) => {
				setLogs(response.data);
				if (logTextArea.current) {
					logTextArea.current.scrollTop = logTextArea.current.scrollHeight;
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
			});
	};

	const DownloadLogs = (id: number) => {
		AxiosInstance.get(`instances/${id}/logs/download/`)
			.then((response) => {
				const blob = new Blob([response.data], { type: "text/plain" });
				const url = window.URL.createObjectURL(blob);
				const a = document.createElement("a");
				a.href = url;
				a.download = `server_logs_${id}.txt`;
				document.body.appendChild(a);
				a.click();
				document.body.removeChild(a);
				window.URL.revokeObjectURL(url);
			})
			.catch((error: any) => {
				console.log(error);
				setAlert(
					error.response.data.message
						? error.response.data.message
						: error.message,
					"error"
				);
			});
	};

	return (
		<>
			<Modal
				aria-labelledby="transition-modal-title"
				aria-describedby="transition-modal-description"
				open={props.selectedInstanceId !== null}
				onClose={props.onClose}
				closeAfterTransition
				slots={{ backdrop: Backdrop }}
				slotProps={{
					backdrop: {
						timeout: 500,
					},
				}}
			>
				<Fade in={props.selectedInstanceId !== null}>
					<Box sx={style}>
						<Box
							sx={{
								display: "flex",
								justifyContent: "center",
								alignItems: "center",
								marginBottom: 2,
							}}
						>
							<Typography
								id="transition-modal-title"
								variant="h5"
								component="h2"
							>
								Logi
							</Typography>
						</Box>
						<Button
							sx={{
								position: "absolute",
								right: "2px",
								top: "4px",
								zIndex: 2,
								padding: "0px",
								minWidth: "0px",
							}}
							onClick={props.onClose}
						>
							<CloseIcon sx={{ color: "red" }} fontSize="medium" />
						</Button>
						{!logs || logs.length === 0 ? (
							<Typography m={2}>Brak logów do wyświetlenia</Typography>
						) : (
							<TextField multiline value={logs} disabled fullWidth rows={25} />
						)}
						<Button
							color="primary"
							aria-label="refresh logs"
							component="label"
							onClick={() => GetLogs(props.selectedInstanceId)}
						>
							<RefreshIcon />
						</Button>
						<Button
							color="primary"
							aria-label="download log file"
							component="label"
							onClick={() => DownloadLogs(props.selectedInstanceId)}
							disabled={!logs || logs.length === 0}
						>
							<FileDownloadIcon />
						</Button>
					</Box>
				</Fade>
			</Modal>
		</>
	);
}
