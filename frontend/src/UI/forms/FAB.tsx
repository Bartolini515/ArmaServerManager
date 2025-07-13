import Box from "@mui/material/Box";
import Fab from "@mui/material/Fab";
import AddIcon from "@mui/icons-material/Add";

interface Props {
	handleClick: () => void;
	color?: "primary" | "secondary" | "inherit" | "default";
	style?: import("@mui/system").SxProps<import("@mui/material").Theme>;
	size?: "small" | "medium" | "large";
}

export default function FAB(props: Props) {
	const style = {
		margin: 0,
		top: "auto",
		right: 20,
		bottom: 20,
		left: "auto",
		position: "fixed",
	};

	return (
		<Box sx={{ "& > :not(style)": { m: 1 } }}>
			<Fab
				size={props.size ? props.size : "medium"}
				color={props.color ? props.color : "primary"}
				aria-label="add"
				sx={props.style ? props.style : style}
				onClick={props.handleClick}
			>
				<AddIcon />
			</Fab>
		</Box>
	);
}
