import { Typography, Box, Avatar } from "@mui/material";
import { useAuth } from "../../contexts/AuthContext";
import MyButton from "../../UI/forms/MyButton";
import { useNavigate } from "react-router-dom";

export default function Account() {
	const navigate = useNavigate();
	const { user } = useAuth();

	return (
		<Box
			sx={{
				boxShadow: 24,
				minWidth: "max-content",
				width: "20%",
				p: 2,
				alignItems: "center",
				flexDirection: "column",
				display: "flex",
				position: "absolute",
				top: "50%",
				left: "50%",
				transform: "translate(-50%, -50%)",
			}}
		>
			<Box position="relative" mb={2}>
				<Avatar
					sx={{
						width: 100,
						height: 100,
					}}
				/>
			</Box>
			<Box display="flex" alignItems="center" mb={1}>
				<Typography variant="h6">{user?.username}</Typography>
			</Box>

			<MyButton
				label="Zmień hasło"
				type="button"
				color="primary"
				onClick={() => {
					navigate("/change_password");
				}}
				style={{ marginTop: "10px" }}
			/>
		</Box>
	);
}
