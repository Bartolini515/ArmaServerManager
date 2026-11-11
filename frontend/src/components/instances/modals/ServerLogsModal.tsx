import { useEffect, useRef, useState } from "react";
import {
	Backdrop,
	Box,
	Button,
	CircularProgress,
	Fade,
	Modal,
	Typography,
	FormControlLabel,
	Switch,
	Select,
	MenuItem,
} from "@mui/material";
import FileDownloadIcon from "@mui/icons-material/FileDownload";
import DeleteForeverIcon from "@mui/icons-material/DeleteForever";
import RefreshIcon from "@mui/icons-material/Refresh";
import CloseIcon from "@mui/icons-material/Close";
import AxiosInstance from "../../AxiosInstance";
import { useAlert } from "../../../contexts/AlertContext";
import { getApiErrorMessage } from "../../../util/api-error";

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
	canDeleteLogs: boolean;
	onDeleteLogs: (instanceId: number) => Promise<boolean>;
};

export default function ServerLogsModal(props: Props) {
	const { setAlert } = useAlert();
	const [logs, setLogs] = useState("");
	const logTextArea = useRef<HTMLPreElement>(null);
	const mountedRef = useRef(false);
	const displayedLength = useRef(0);
	const [tailLines, setTailLines] = useState(() => {
		const value = Number(localStorage.getItem("logTailLines"));
		return [100, 500, 1000, 2000].includes(value) ? value : 2000;
	});
	const [autoRefresh, setAutoRefresh] = useState(() => localStorage.getItem("logAutoRefresh") === "true");
	const [refresh, setRefresh] = useState(0);
	const [logError, setLogError] = useState("");
	const [truncated, setTruncated] = useState(false);
	const [loading, setLoading] = useState(true);

	useEffect(() => {
		mountedRef.current = true;
		return () => {
			mountedRef.current = false;
		};
	}, []);

	useEffect(() => {
		localStorage.setItem("logTailLines", String(tailLines));
		localStorage.setItem("logAutoRefresh", String(autoRefresh));
		let active = true;
		let timer: ReturnType<typeof setTimeout>;
		let cursor: string | undefined;
		const controller = new AbortController();
		setLoading(true);
		const poll = async () => {
			let delay = 1000;
			try {
				const response = await AxiosInstance.get(`instances/${props.selectedInstanceId}/logs/incremental/`, {
					params: { tail: tailLines, cursor }, signal: controller.signal,
				});
				if (!active) return;
				const data = response.data as { content: string; cursor: string; reset: boolean; truncated: boolean; has_more: boolean };
				const element = logTextArea.current;
				const follow = !element || element.scrollHeight - element.scrollTop - element.clientHeight < 20;
				const replace = !cursor || data.reset;
				cursor = data.cursor;
				const clipped = !replace && displayedLength.current + data.content.length > 262144;
				setLogs(previous => {
					const text = (replace ? data.content : previous + data.content).slice(-262144);
					const result = text.split("\n").slice(-(tailLines + 1)).join("\n");
					displayedLength.current = result.length;
					return result;
				});
				setTruncated(previous => replace ? data.truncated : previous || clipped);
				setLogError("");
				if (follow) requestAnimationFrame(() => { if (logTextArea.current) logTextArea.current.scrollTop = logTextArea.current.scrollHeight; });
				if (data.has_more) delay = 0;
			} catch (error: unknown) {
				if (active) setLogError(getApiErrorMessage(error, "Nie udało się odświeżyć logów."));
			} finally {
				if (active) {
					setLoading(false);
					if (autoRefresh) timer = setTimeout(poll, delay);
				}
			}
		};
		void poll();
		return () => { active = false; clearTimeout(timer); controller.abort(); };
	}, [props.selectedInstanceId, tailLines, autoRefresh, refresh]);

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
			.catch((error: unknown) => {
				console.log(error);
				setAlert(getApiErrorMessage(error, "Wystąpił błąd."), "error");
			});
	};

	const DeleteLogs = async (id: number) => {
		if (!props.canDeleteLogs) {
			return;
		}
		const deleted = await props.onDeleteLogs(id);
		if (
			deleted &&
			mountedRef.current &&
			props.selectedInstanceId === id
		) {
			setLogs("");
		}
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
						<Select size="small" aria-label="Liczba wierszy" value={tailLines} onChange={event => setTailLines(Number(event.target.value))}>
							{[100, 500, 1000, 2000].map(value => <MenuItem key={value} value={value}>{value} wierszy</MenuItem>)}
						</Select>
						<FormControlLabel control={<Switch checked={autoRefresh} onChange={event => setAutoRefresh(event.target.checked)} />} label="Automatyczne odświeżanie" />
						{logError && <Typography color="error">{logError}</Typography>}
						{truncated && <Typography>Starsza część logu została ucięta.</Typography>}
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
							onClick={() => setRefresh(value => value + 1)}
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
							disabled={!props.canDeleteLogs || !logs || logs.length === 0}
						>
							<DeleteForeverIcon />
						</Button>
					</Box>
				</Fade>
			</Modal>
		</>
	);
}
