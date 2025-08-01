import * as React from "react";
import Box from "@mui/material/Box";
import Avatar from "@mui/material/Avatar";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import ListItemIcon from "@mui/material/ListItemIcon";
import Divider from "@mui/material/Divider";
import IconButton from "@mui/material/IconButton";
import Tooltip from "@mui/material/Tooltip";
// import Settings from "@mui/icons-material/Settings";
import Logout from "@mui/icons-material/Logout";
import AxiosInstance from "../AxiosInstance";
import { useNavigate } from "react-router-dom";
import { useAlert } from "../../contexts/AlertContext";
import { useAuth } from "../../contexts/AuthContext";

export default function AccountMenu() {
	const [anchorEl, setAnchorEl] = React.useState<null | HTMLElement>(null);
	const open = Boolean(anchorEl);
	const handleClick = (event: React.MouseEvent<HTMLElement>) => {
		setAnchorEl(event.currentTarget);
	};
	const handleClose = () => {
		setAnchorEl(null);
	};
	const navigate = useNavigate();
	const { setAlert } = useAlert();
	const { user } = useAuth();

	const logoutUser = () => {
		AxiosInstance.post(`logout/`, {}).then(() => {
			localStorage.removeItem("Token");
			navigate("/");
			setAlert("Wylogowano pomyślnie", "success");
		});
	};

	return (
		<>
			<Box sx={{ display: "flex", alignItems: "center", textAlign: "center" }}>
				<Tooltip title="Konto">
					<IconButton
						onClick={handleClick}
						size="small"
						sx={{ ml: 2 }}
						aria-controls={open ? "account-menu" : undefined}
						aria-haspopup="true"
						aria-expanded={open ? "true" : undefined}
					>
						<Avatar sx={{ width: 32, height: 32 }}></Avatar>
					</IconButton>
				</Tooltip>
			</Box>
			<Menu
				anchorEl={anchorEl}
				id="account-menu"
				open={open}
				onClose={handleClose}
				onClick={handleClose}
				slotProps={{
					paper: {
						elevation: 0,
						sx: {
							overflow: "visible",
							filter: "drop-shadow(0px 2px 8px rgba(0,0,0,0.32))",
							mt: 1.5,
							"& .MuiAvatar-root": {
								width: 32,
								height: 32,
								ml: -0.5,
								mr: 1,
							},
							"&::before": {
								content: '""',
								display: "block",
								position: "absolute",
								top: 0,
								right: 14,
								width: 10,
								height: 10,
								bgcolor: "background.paper",
								transform: "translateY(-50%) rotate(45deg)",
								zIndex: 0,
							},
						},
					},
				}}
				transformOrigin={{ horizontal: "right", vertical: "top" }}
				anchorOrigin={{ horizontal: "right", vertical: "bottom" }}
			>
				<MenuItem
					onClick={() => {
						handleClose;
						navigate("/account");
					}}
				>
					<Avatar /> {user?.username}
				</MenuItem>
				<Divider />
				{/* <MenuItem onClick={handleClose}>
					<ListItemIcon>
						<Settings fontSize="small" />
					</ListItemIcon>
					Ustawienia
				</MenuItem> */}
				<MenuItem
					onClick={() => {
						handleClose();
						logoutUser();
					}}
				>
					<ListItemIcon>
						<Logout fontSize="small" />
					</ListItemIcon>
					Wyloguj
				</MenuItem>
			</Menu>
		</>
	);
}
