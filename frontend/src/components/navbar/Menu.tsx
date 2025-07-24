import { Link, useLocation } from "react-router";
import List from "@mui/material/List";
import ListItemButton from "@mui/material/ListItemButton";
import ListItemIcon from "@mui/material/ListItemIcon";
import ListItemText from "@mui/material/ListItemText";
import Collapse from "@mui/material/Collapse";
import ExpandLess from "@mui/icons-material/ExpandLess";
import ExpandMore from "@mui/icons-material/ExpandMore";
import StarBorder from "@mui/icons-material/StarBorder";
import DashboardIcon from "@mui/icons-material/Dashboard";
import StorageIcon from "@mui/icons-material/Storage";
import AdminPanelSettingsIcon from "@mui/icons-material/AdminPanelSettings";
import DatasetOutlinedIcon from "@mui/icons-material/DatasetOutlined";
import SettingsOutlinedIcon from "@mui/icons-material/SettingsOutlined";
import { useState } from "react";
import { useAuth } from "../../contexts/AuthContext";

export default function Menu() {
	const [openModerator, setOpenModerator] = useState(false);
	const { isAdmin } = useAuth();

	const handleClickModerator = () => {
		setOpenModerator(!openModerator);
	};

	const location = useLocation();
	const path = location.pathname;

	return (
		<List
			sx={{
				width: "100%",
				maxWidth: 360,
				bgcolor: "background.paper",
				color: "rgba(0, 0, 0, 0.54)",
			}}
			component="nav"
		>
			{/* TODO: Change every path to actual on this project */}
			<ListItemButton
				component={Link}
				to="/dashboard"
				selected={path === "/dashboard"}
			>
				<ListItemIcon>
					<DashboardIcon />
				</ListItemIcon>
				<ListItemText primary="Pulpit" />
			</ListItemButton>

			<ListItemButton
				component={Link}
				to="/instances"
				selected={path === "/instances"}
			>
				<ListItemIcon>
					<StorageIcon />
				</ListItemIcon>
				<ListItemText primary="Instancje serwerowe" />
			</ListItemButton>

			<ListItemButton
				onClick={handleClickModerator}
				selected={
					path === "/moderator_panel_users" ||
					path === "/moderator_panel_event_types"
				}
				sx={{ display: isAdmin ? "flex" : "none" }}
			>
				<ListItemIcon>
					<AdminPanelSettingsIcon />
				</ListItemIcon>
				<ListItemText primary="Panel moderatora" />
				{openModerator ? <ExpandLess /> : <ExpandMore />}
			</ListItemButton>
			<Collapse in={openModerator} timeout="auto" unmountOnExit>
				<List component="div" disablePadding>
					<ListItemButton
						sx={{ pl: 3, display: isAdmin ? "flex" : "none" }}
						component={Link}
						to="/moderator_panel_data_management"
						selected={path === "/moderator_panel_data_management"}
					>
						<DatasetOutlinedIcon>
							<StarBorder />
						</DatasetOutlinedIcon>
						<ListItemText primary="Zarządzanie danymi" />
					</ListItemButton>
					<ListItemButton
						sx={{ pl: 3, display: isAdmin ? "flex" : "none" }}
						component={Link}
						to="/moderator_panel_config"
						selected={path === "/moderator_panel_config"}
					>
						<SettingsOutlinedIcon>
							<StarBorder />
						</SettingsOutlinedIcon>
						<ListItemText primary="Konfiguracja" />
					</ListItemButton>
				</List>
			</Collapse>
		</List>
	);
}
