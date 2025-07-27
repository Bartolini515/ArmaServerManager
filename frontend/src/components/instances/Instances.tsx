import { Divider, Skeleton, Stack, Typography } from "@mui/material";
import Box from "@mui/material/Box";
import UserInstances from "./UserInstances";
import AdminInstances from "./AdminInstances";
import AxiosInstance from "../AxiosInstance";
import { useEffect, useState } from "react";
import { useAuth } from "../../contexts/AuthContext";
import { useAlert } from "../../contexts/AlertContext";
import FAB from "../../UI/forms/FAB";
import CreateInstanceModal from "./modals/CreateInstanceModal";

interface Instance {
	id: number;
	name: string;
	user: string;
	preset: string;
	log_file: string | null;
	start_file_path: string;
	port: number;
	pid: number;
	is_ready: boolean;
	is_running: boolean;
	is_admin_instance: boolean;
	created_at: string;
}

export default function Instances() {
	const [openCreateInstanceModal, setOpenCreateInstanceModal] = useState(false);
	const [userInstances, setUserInstances] = useState<Instance[]>([]);
	const [adminInstances, setAdminInstances] = useState<Instance[]>([]);
	const [refresh, setRefresh] = useState(false);

	const { user, isAdmin } = useAuth();
	const { setAlert } = useAlert();
	const [loading, setLoading] = useState(true);

	const GetData = () => {
		AxiosInstance.get("instances/")
			.then((response) => {
				setUserInstances(response.data.user_instances);

				if (isAdmin) {
					setAdminInstances(response.data.admin_instances);
				}
				setLoading(false);
			})
			.catch((error: any) => {
				console.log(error);
				setAlert("Unable to retrieve system information", "error");
			});
	};

	useEffect(() => {
		GetData();
		setRefresh(false);
	}, [refresh]);

	const handleClickFAB = () => {
		setOpenCreateInstanceModal(true);
	};

	return (
		<>
			<Stack spacing={2} alignItems="center">
				<Box sx={{ width: "100%" }}>
					<Divider sx={{ marginY: 2 }} />
					<Typography align="center" fontWeight={700} fontSize={24}>
						Instancje Użytkownika
					</Typography>
					<Divider sx={{ marginY: 2 }} />
					{loading ? (
						<Skeleton variant="rectangular" height={200} />
					) : (
						<UserInstances
							userInstances={userInstances}
							setRefresh={setRefresh}
						/>
					)}
				</Box>
				{isAdmin ? (
					<Box sx={{ width: "100%" }}>
						<Divider sx={{ marginY: 2 }} />
						<Typography align="center" fontWeight={700} fontSize={24}>
							Instancje Główne
						</Typography>
						<Divider sx={{ marginY: 2 }} />
						{loading ? (
							<Skeleton variant="rectangular" height={200} />
						) : (
							<AdminInstances adminInstances={adminInstances} />
						)}
					</Box>
				) : null}
			</Stack>
			{openCreateInstanceModal && (
				<CreateInstanceModal
					open={openCreateInstanceModal}
					onClose={() => {
						setOpenCreateInstanceModal(false), setRefresh(true);
					}}
				/>
			)}
			<FAB handleClick={handleClickFAB} />
		</>
	);
}
