import Backdrop from "@mui/material/Backdrop";
import Box from "@mui/material/Box";
import Modal from "@mui/material/Modal";
import Fade from "@mui/material/Fade";
import AxiosInstance from "../AxiosInstance";
import { Button, CircularProgress, Typography } from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import { useForm } from "react-hook-form";
import { useAlert } from "../../contexts/AlertContext";
import MyDropzone from "../../UI/forms/MyDropzone";
import { useState } from "react";

const style = {
	position: "absolute",
	top: "50%",
	left: "50%",
	transform: "translate(-50%, -50%)",
	width: { xs: "80%", sm: "600px" },
	height: "90%",
	maxHeight: "max-content",
	overflow: "auto",
	bgcolor: "background.paper",
	border: "2px solid #000",
	borderRadius: 4,
	boxShadow: 24,
	p: 4,
};

interface Props {
	open: boolean;
	onClose: () => void;
}

interface FormData {
	mission_file: File | null;
}

export default function CreateMissionModal(props: Props) {
	const [loading, setLoading] = useState(false);
	const { handleSubmit, control, setError, clearErrors, setValue } =
		useForm<FormData>({
			defaultValues: {
				mission_file: null,
			},
		});

	const { setAlert } = useAlert();

	const submission = (data: FormData) => {
		setLoading(true);
		setAlert("Wgrywanie misji, proszę czekać...", "info");
		AxiosInstance.post(`/missions/`, data, {
			headers: {
				"Content-Type": "multipart/form-data",
			},
		})
			.then((response) => {
				props.onClose();
				setAlert(response.data.message, "success");
			})
			.catch((error: any) => {
				if (
					error.response &&
					error.response.data &&
					error.response.status === 400
				) {
					const serverErrors = error.response.data;
					Object.keys(serverErrors).forEach((field) => {
						setError(field as keyof FormData, {
							type: "server",
							message: serverErrors[field][0],
						});
					});
				} else {
					console.log(error);
					setAlert(error.response.data.message || error.message, "error");
				}
			})
			.finally(() => {
				setLoading(false);
			});
	};

	return (
		<>
			<Modal
				aria-labelledby="transition-modal-title"
				aria-describedby="transition-modal-description"
				open={props.open}
				onClose={loading ? () => {} : props.onClose}
				closeAfterTransition
				slots={{ backdrop: Backdrop }}
				slotProps={{
					backdrop: {
						timeout: 500,
					},
				}}
			>
				<Fade in={props.open}>
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
								Wgraj misję
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
						<form>
							{loading ? (
								<Box
									sx={{
										display: "flex",
										justifyContent: "center",
										alignItems: "center",
										minHeight: "200px",
									}}
								>
									<CircularProgress />
								</Box>
							) : (
								<MyDropzone
									label={"Prześlij misję"}
									name={"mission_file"}
									control={control}
									multiple={false}
									maxFiles={1}
									onSubmit={(files) => {
										clearErrors();
										if (files && files.length > 0) {
											setValue("mission_file", files[0]);
										}
										handleSubmit((data) => {
											submission(data);
										})();
									}}
									style={{
										width: "100%",
										minHeight: "200px",
										textAlign: "center",
										display: "flex",
										justifyContent: "center",
										alignItems: "center",
									}}
								></MyDropzone>
							)}
						</form>
					</Box>
				</Fade>
			</Modal>
		</>
	);
}
