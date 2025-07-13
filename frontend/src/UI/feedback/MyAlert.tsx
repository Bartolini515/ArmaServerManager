import { useEffect } from "react";
import Snackbar from "@mui/material/Snackbar";
import Alert from "@mui/material/Alert";

interface Props {
    message: string;
    severity: "error" | "warning" | "info" | "success";
    timeout: number;
    setAlertOpen: (open: boolean) => void;
    alertOpen: boolean;
}

export default function MyAlert(props: Props) {
    useEffect(() => {
        if (props.alertOpen) {
            const timer = setTimeout(() => {
                props.setAlertOpen(false);
            }, props.timeout);

            return () => clearTimeout(timer);
        }
    }, [props.alertOpen, props.timeout, props.setAlertOpen]);

    return (
        <Snackbar
            open={props.alertOpen}
            autoHideDuration={props.timeout}
            onClose={() => props.setAlertOpen(false)}
            anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
        >
            <Alert
                onClose={() => props.setAlertOpen(false)}
                severity={props.severity}
                variant="filled"
            >
                {props.message}
            </Alert>
        </Snackbar>
    );
}