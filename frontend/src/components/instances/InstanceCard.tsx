import {
	Button,
	Card,
	CardActions,
	CardContent,
	Typography,
} from "@mui/material";

interface Props {
	title: string;
}

export default function InstanceCard(props: Props) {
	return (
		<Card sx={{ minWidth: { xs: 300, sm: 400, md: 800 } }}>
			<CardContent>
				<Typography variant="h5" component="div">
					{props.title}
				</Typography>
				<Typography sx={{ mb: 1.5 }} color="text.secondary">
					Status: Running
				</Typography>
				<Typography variant="body2">
					Some additional information about the instance can go here.
					<br />
					- Modset: Example Modset
					<br />- Players: 10/70
				</Typography>
			</CardContent>
			<CardActions>
				<Button size="small">Manage</Button>
				<Button size="small" color="error">
					Stop
				</Button>
			</CardActions>
		</Card>
	);
}
