import Box from "@mui/material/Box";
import { useState } from "react";
import { Skeleton } from "@mui/material";
import SystemResourcesMonitor from "./SystemResourcesMonitor";

export default function Dashboard() {
	const [loading, setLoading] = useState(false);

	return (
		<Box
			sx={{
				flexGrow: { sm: 1, xs: "none" },
				padding: 2,
				alignContent: "center",
			}}
		>
			<Box>
				{loading ? (
					<Skeleton variant="rectangular" height={300} />
				) : (
					<SystemResourcesMonitor loading={loading} setLoading={setLoading} />
				)}
			</Box>
		</Box>
	);
}
