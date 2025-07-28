import Backdrop from "@mui/material/Backdrop";
import Box from "@mui/material/Box";
import Modal from "@mui/material/Modal";
import Fade from "@mui/material/Fade";
import AxiosInstance from "../../AxiosInstance";
import { Button, Typography } from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import { useForm } from "react-hook-form";
import { useAlert } from "../../../contexts/AlertContext";
import MyDropzone from "../../../UI/forms/MyDropzone";

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
	selectedAdminInstanceId: number | null;
}

interface FormData {
	preset: File | null;
}

export default function ChangePresetModal(props: Props) {
	const { handleSubmit, control, setError, clearErrors, setValue } =
		useForm<FormData>({
			defaultValues: {
				preset: null,
			},
		});

	const { setAlert } = useAlert();

	const submission = (data: FormData) => {
		AxiosInstance.post(
			`instances/${props.selectedAdminInstanceId}/admin_instance/change_preset/`,
			data,
			{
				headers: {
					"Content-Type": "multipart/form-data",
				},
			}
		)
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
			});
	};

	return (
		<>
			<Modal
				aria-labelledby="transition-modal-title"
				aria-describedby="transition-modal-description"
				open={props.open}
				onClose={props.onClose}
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
								Zmień preset
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
							<MyDropzone
								label={"Prześlij preset"}
								name={"preset"}
								control={control}
								multiple={false}
								maxFiles={1}
								accept={"text/html"}
								onSubmit={(files) => {
									clearErrors();
									if (files && files.length > 0) {
										setValue("preset", files[0]);
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
						</form>
					</Box>
				</Fade>
			</Modal>
		</>
	);
}
