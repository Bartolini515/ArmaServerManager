import Backdrop from "@mui/material/Backdrop";
import Box from "@mui/material/Box";
import Modal from "@mui/material/Modal";
import Fade from "@mui/material/Fade";
import AxiosInstance from "../../AxiosInstance";
import { Button, Typography } from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import MyTextField from "../../../UI/forms/MyTextField";
import { useForm } from "react-hook-form";
import MyButton from "../../../UI/forms/MyButton";
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
}

interface FormData {
	name: string;
	preset: File | null;
}

export default function CreateInstanceModal(props: Props) {
	const { handleSubmit, control, setError, clearErrors, setValue } =
		useForm<FormData>({
			defaultValues: {
				name: "",
				preset: null,
			},
		});

	const { setAlert } = useAlert();

	const submission = (data: FormData) => {
		AxiosInstance.post(`/instances/`, data, {
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
			});
	};

	const handleClick = () => {
		clearErrors();
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
								Stwórz instancje
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
						<form onSubmit={handleSubmit(submission)}>
							<Box
								sx={{
									boxShadow: 3,
									padding: "20px",
									display: "flex",
									flexDirection: "row",
									marginBottom: "20px",
								}}
							>
								<Box
									sx={{
										fontWeight: "bold",
										alignContent: "center",
									}}
								>
									Nazwa instancji
								</Box>
								<Box sx={{ marginLeft: "10px" }}>
									<MyTextField
										label="Nazwa instancji"
										name="name"
										control={control}
									/>
								</Box>
							</Box>

							<Box
								sx={{
									boxShadow: 3,
									padding: "20px",
									display: "flex",
									flexDirection: "column",
									marginBottom: "20px",
								}}
							>
								<Typography
									id="transition-modal-title"
									variant="h5"
									component="h2"
									sx={{
										marginBottom: "15px",
										textAlign: "center",
									}}
								>
									Preset modyfikacji
								</Typography>

								<MyDropzone
									label={"Prześlij preset"}
									name={"preset"}
									control={control}
									multiple={false}
									maxFiles={1}
									onSubmit={(files) => {
										if (files && files.length > 0) {
											setValue("preset", files[0]);
										}
									}}
									accept={["text/html"]}
									style={{
										width: "100%",
										minHeight: "200px",
										textAlign: "center",
										display: "flex",
										justifyContent: "center",
										alignItems: "center",
									}}
								></MyDropzone>
							</Box>

							<MyButton
								label="Stwórz"
								type="submit"
								onClick={handleClick}
								style={{ width: "100%" }}
							/>
						</form>
					</Box>
				</Fade>
			</Modal>
		</>
	);
}
