import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogContentText from "@mui/material/DialogContentText";
import DialogTitle from "@mui/material/DialogTitle";

interface Props {
	open: boolean;
	onClose: () => void;
	label?: string;
	content?: string;
	onCloseOption1?: () => void;
	onCloseOption2?: () => void;
	option1?: string;
	option2?: string;
}

export default function AlertDialog(props: Props) {
	return (
		<>
			<Dialog
				open={props.open}
				onClose={props.onClose}
				aria-labelledby="alert-dialog-title"
				aria-describedby="alert-dialog-description"
			>
				<DialogTitle id="alert-dialog-title">
					{props.label || "Potwierdź akcję"}
				</DialogTitle>
				<DialogContent>
					<DialogContentText id="alert-dialog-description">
						{props.content || "Czy na pewno chcesz wykonać tę akcję?"}
					</DialogContentText>
				</DialogContent>
				<DialogActions>
					<Button onClick={props.onCloseOption1 || props.onClose}>
						{props.option1 || "Anuluj"}
					</Button>
					<Button onClick={props.onCloseOption2 || props.onClose} autoFocus>
						{props.option2 || "Potwierdź"}
					</Button>
				</DialogActions>
			</Dialog>
		</>
	);
}
