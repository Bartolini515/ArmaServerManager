import { useEffect, useState } from "react";
import Paper from "@mui/material/Paper";
import { Divider, Skeleton, Stack } from "@mui/material";
import CircularProgressWithLabel from "../../UI/feedback/CircularProgressWithLabel";
// import { getSystemInfo } from "../../services/systemService";
import { useInterval } from "../../hooks/use-interval";
import { humanFileSize } from "../../util/util";
import type { CircularProgressProps } from "@mui/material/CircularProgress/CircularProgress";
import AxiosInstance from "../AxiosInstance";
import { useAlert } from "../../contexts/AlertContext";

interface SystemInfo {
	cpuUsage: number;
	memoryLeft: number;
	memoryTotal: number;
	spaceLeft: number;
	spaceTotal: number;
	cpuCount: number;
	osName: string;
}

export default function SystemResourcesMonitor() {
	const [loading, setLoading] = useState(true);
	const [systemInfo, setSystemInfo] = useState<SystemInfo>({
		cpuUsage: 0,
		memoryLeft: 0,
		memoryTotal: 0,
		spaceLeft: 0,
		spaceTotal: 0,
		cpuCount: 0,
		osName: "",
	});

	const { setAlert } = useAlert();

	useEffect(() => {
		GetData();
	}, []);

	useInterval(() => GetData(), 3000);

	const GetData = () => {
		AxiosInstance.get("services/get_system_info/")
			.then((response) => {
				setSystemInfo(response.data);
				setLoading(false);
			})
			.catch((error: any) => {
				console.log(error);
				setAlert("Unable to retrieve system information", "error");
			});
	};

	const memoryUsedPercent = Math.round(
		((systemInfo.memoryTotal - systemInfo.memoryLeft) /
			systemInfo.memoryTotal) *
			100
	);
	const storageUsedPercent = Math.round(
		((systemInfo.spaceTotal - systemInfo.spaceLeft) / systemInfo.spaceTotal) *
			100
	);

	const evaluateColor = (percent: number): CircularProgressProps["color"] => {
		if (percent < 70) {
			return "primary";
		}
		if (percent < 90) {
			return "warning";
		}
		return "error";
	};

	return loading ? (
		<Skeleton variant="rectangular" height={200} />
	) : (
		<Paper>
			<Stack
				direction={{ xs: "column", sm: "row" }}
				divider={<Divider orientation="vertical" flexItem />}
				p={4}
				spacing={3}
				alignItems="center"
				justifyContent="center"
			>
				<Stack
					direction="row"
					divider={<Divider orientation="vertical" flexItem />}
					spacing={3}
					alignItems="center"
					justifyContent="center"
				>
					<Stack spacing={2} alignItems="center" justifyContent="center">
						<p>CPU usage</p>
						<div>
							<CircularProgressWithLabel
								value={systemInfo.cpuUsage}
								color={evaluateColor(systemInfo.cpuUsage)}
							/>
						</div>
					</Stack>

					<Stack spacing={2} alignItems="center" justifyContent="center">
						<p>Memory</p>
						<div>
							<CircularProgressWithLabel
								value={memoryUsedPercent}
								color={evaluateColor(memoryUsedPercent)}
							/>
						</div>
					</Stack>

					<Stack spacing={2} alignItems="center" justifyContent="center">
						<p>Storage</p>
						<div>
							<CircularProgressWithLabel
								value={storageUsedPercent}
								color={evaluateColor(storageUsedPercent)}
							/>
						</div>
					</Stack>
				</Stack>
				<Stack>
					<p>
						Memory used:{" "}
						<strong>
							{humanFileSize(systemInfo.memoryTotal - systemInfo.memoryLeft)}
						</strong>
					</p>
					<p>
						Total memory:{" "}
						<strong>{humanFileSize(systemInfo.memoryTotal)}</strong>
					</p>
				</Stack>
				<Stack>
					<p>
						Storage used:{" "}
						<strong>
							{humanFileSize(systemInfo.spaceTotal - systemInfo.spaceLeft)}
						</strong>
					</p>
					<p>
						Total storage:{" "}
						<strong>{humanFileSize(systemInfo.spaceTotal)}</strong>
					</p>
				</Stack>
				<Stack>
					<p>
						OS: <strong>{systemInfo.osName}</strong>
					</p>
					<p>
						CPU count: <strong>{systemInfo.cpuCount}</strong>
					</p>
				</Stack>
			</Stack>
		</Paper>
	);
}
