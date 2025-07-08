import { Link, useLocation } from "react-router";
import List from "@mui/material/List";
import ListItemButton from "@mui/material/ListItemButton";
import ListItemIcon from "@mui/material/ListItemIcon";
import ListItemText from "@mui/material/ListItemText";
import Collapse from "@mui/material/Collapse";
import ExpandLess from "@mui/icons-material/ExpandLess";
import ExpandMore from "@mui/icons-material/ExpandMore";
import StarBorder from "@mui/icons-material/StarBorder";
import GridViewIcon from "@mui/icons-material/GridView";
import CalendarMonthIcon from "@mui/icons-material/CalendarMonth";
import ScheduleIcon from "@mui/icons-material/Schedule";
import SchoolIcon from "@mui/icons-material/School";
import EditNoteIcon from "@mui/icons-material/EditNote";
import AdminPanelSettingsIcon from "@mui/icons-material/AdminPanelSettings";
import DatasetOutlinedIcon from "@mui/icons-material/DatasetOutlined";
import SettingsOutlinedIcon from "@mui/icons-material/SettingsOutlined";
import { useState } from "react";
import { useAuth } from "../../contexts/AuthContext";

export default function Menu() {
	const [openEvents, setOpenEvents] = useState(false);
	const [openModerator, setOpenModerator] = useState(false);
	const { isAdmin } = useAuth();

	const handleClickEvents = () => {
		setOpenEvents(!openEvents);
	};

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
					<GridViewIcon />
				</ListItemIcon>
				<ListItemText primary="Pulpit" />
			</ListItemButton>
			<ListItemButton
				onClick={handleClickEvents}
				selected={path === "/timetable" || path === "/tasks"}
			>
				<ListItemIcon>
					<ScheduleIcon />
				</ListItemIcon>
				<ListItemText primary="Wydarzenia" />
				{openEvents ? <ExpandLess /> : <ExpandMore />}
			</ListItemButton>
			<Collapse in={openEvents} timeout="auto" unmountOnExit>
				<List component="div" disablePadding>
					<ListItemButton
						sx={{ pl: 3 }}
						component={Link}
						to="/timetable"
						selected={path === "/timetable"}
					>
						<CalendarMonthIcon>
							<StarBorder />
						</CalendarMonthIcon>
						<ListItemText primary="Harmonogram" />
					</ListItemButton>
				</List>
			</Collapse>
			<Collapse in={openEvents} timeout="auto" unmountOnExit>
				<List component="div" disablePadding>
					<ListItemButton
						sx={{ pl: 3 }}
						component={Link}
						to="/tasks"
						selected={path === "/tasks"}
					>
						<EditNoteIcon>
							<StarBorder />
						</EditNoteIcon>
						<ListItemText primary="Zadania" />
					</ListItemButton>
				</List>
			</Collapse>

			<ListItemButton
				component={Link}
				to="/schedule"
				selected={path === "/schedule"}
			>
				<ListItemIcon>
					<SchoolIcon />
				</ListItemIcon>
				<ListItemText primary="Plan lekcji" />
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
