import { useEffect, useRef, useState } from "react";
import {
	Backdrop,
	Box,
	Button,
	CircularProgress,
	Fade,
	Modal,
	Typography,
} from "@mui/material";
import FileDownloadIcon from "@mui/icons-material/FileDownload";
import DeleteForeverIcon from "@mui/icons-material/DeleteForever";
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
	// const [tailLines, setTailLines] = useState(1000);
	const [loading, setLoading] = useState(true);

	useEffect(() => {
		if (props.selectedInstanceId !== null) {
			GetLogs(props.selectedInstanceId);
		}
	}, [props.selectedInstanceId]);

	const GetLogs = (id: number) => {
		AxiosInstance.get(`instances/${id}/logs/`, { params: { tail: 2000 } })
			.then((response) => {
				const newLogs: string = response.data;
				setLogs((prev) => (prev === newLogs ? prev : newLogs));
				if (logTextArea.current) {
					logTextArea.current.scrollTop = logTextArea.current.scrollHeight;
				}
				setLoading(false);
			})
			.catch((error: any) => {
				console.log(error);
				setAlert(
					error.response?.data?.message
						? error.response.data.message
						: error.message,
					"error"
				);
				setLoading(false);
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
					error.response?.data?.message
						? error.response.data.message
						: error.message,
					"error"
				);
			});
	};

	const DeleteLogs = (id: number) => {
		AxiosInstance.delete(`instances/${id}/logs/delete/`)
			.then((response) => {
				setLogs("");
				setAlert(response.data.message, "success");
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
						{loading ? (
							<Box sx={{ display: "flex", justifyContent: "center", mt: 2 }}>
								<CircularProgress />
							</Box>
						) : !logs || logs.length === 0 ? (
							<Typography m={2}>Brak logów do wyświetlenia</Typography>
						) : (
							<Box
								component="pre"
								ref={logTextArea}
								sx={{
									fontFamily: "monospace",
									fontSize: "0.75rem",
									m: 0,
									p: 1,
									maxHeight: "60vh",
									overflowY: "auto",
									whiteSpace: "pre-wrap",
									backgroundColor: "#111",
									color: "#ddd",
									border: "1px solid #333",
									borderRadius: 1,
								}}
							>
								{logs}
							</Box>
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
						<Button
							color="primary"
							aria-label="delete log file"
							component="label"
							onClick={() => DeleteLogs(props.selectedInstanceId)}
							disabled={!logs || logs.length === 0}
						>
							<DeleteForeverIcon />
						</Button>
					</Box>
				</Fade>
			</Modal>
		</>
	);
}
