import { createContext, useState, useContext, type ReactNode } from "react";
import MyAlert from "../components/feedback/MyAlert";

interface AlertContextType {
	alertOpen: boolean;
	alertMessage: string;
	alertSeverity: "error" | "warning" | "info" | "success";
	alertTimeout: number;
	setAlert: (
		message: string,
		severity: "error" | "warning" | "info" | "success",
		timeout?: number
	) => void;
	closeAlert: () => void;
}

const AlertContext = createContext<AlertContextType | undefined>(undefined);

export const AlertProvider = ({ children }: { children: ReactNode }) => {
	const [alertOpen, setAlertOpen] = useState<boolean>(false);
	const [alertMessage, setAlertMessage] = useState<string>("");
	const [alertSeverity, setAlertSeverity] = useState<
		"error" | "warning" | "info" | "success"
	>("error");
	const [alertTimeout, setAlertTimeout] = useState<number>(8000);

	const setAlert = (
		message: string,
		severity: "error" | "warning" | "info" | "success",
		timeout: number = 8000
	) => {
		setAlertMessage(message);
		setAlertSeverity(severity);
		setAlertTimeout(timeout);
		setAlertOpen(true);
	};

	const closeAlert = () => {
		setAlertOpen(false);
	};

	return (
		<AlertContext.Provider
			value={{
				alertOpen,
				alertMessage,
				alertSeverity,
				alertTimeout,
				setAlert,
				closeAlert,
			}}
		>
			{children}
			<MyAlert
				message={alertMessage}
				severity={alertSeverity}
				timeout={alertTimeout}
				alertOpen={alertOpen}
				setAlertOpen={setAlertOpen}
			/>
		</AlertContext.Provider>
	);
};

export const useAlert = () => {
	const context = useContext(AlertContext);
	if (!context) {
		throw new Error("useAlert must be used within an AlertProvider");
	}
	return context;
};
