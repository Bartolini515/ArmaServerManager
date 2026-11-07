import {
	createContext,
	useState,
	useContext,
	useCallback,
	useEffect,
	useRef,
	type ReactNode,
} from "react";

interface AuthContextType {
	token: string | null;
	setToken: (token: string | null) => void;
	isAdmin: boolean;
	setIsAdmin: (isAdmin: boolean) => void;
	user: UserType | null;
	setUser: (user: UserType | null) => void;
}

export interface UserType {
	id: number;
	username: string;
	last_login: string;
	profile_picture?: string | null;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider = ({ children }: { children: ReactNode }) => {
	const [token, setTokenState] = useState<string | null>(() =>
		localStorage.getItem("Token"),
	);
	const tokenRef = useRef(token);
	const [isAdmin, setIsAdmin] = useState<boolean>(false);
	const [user, setUser] = useState<UserType | null>(null);
	const applyTokenChange = useCallback((nextToken: string | null) => {
		if (tokenRef.current === nextToken) {
			return;
		}

		tokenRef.current = nextToken;
		setTokenState(nextToken);
		setUser(null);
		setIsAdmin(false);
	}, [setIsAdmin, setUser]);
	const setToken = useCallback(
		(nextToken: string | null) => {
			if (nextToken) {
				localStorage.setItem("Token", nextToken);
			} else {
				localStorage.removeItem("Token");
			}
			applyTokenChange(nextToken);
		},
		[applyTokenChange],
	);

	useEffect(() => {
		const handleSessionInvalidated = () => {
			applyTokenChange(localStorage.getItem("Token"));
		};
		const handleStorage = (event: StorageEvent) => {
			if (event.key === "Token" || event.key === null) {
				applyTokenChange(localStorage.getItem("Token"));
			}
		};

		window.addEventListener("auth:session-invalidated", handleSessionInvalidated);
		window.addEventListener("storage", handleStorage);
		return () => {
			window.removeEventListener(
				"auth:session-invalidated",
				handleSessionInvalidated,
			);
			window.removeEventListener("storage", handleStorage);
		};
	}, [applyTokenChange]);

	return (
		<AuthContext.Provider
			value={{ token, setToken, isAdmin, setIsAdmin, user, setUser }}
		>
			{children}
		</AuthContext.Provider>
	);
};

export const useAuth = () => {
	const context = useContext(AuthContext);
	if (!context) {
		throw new Error("useAuth must be used within an AuthProvider");
	}
	return context;
};
