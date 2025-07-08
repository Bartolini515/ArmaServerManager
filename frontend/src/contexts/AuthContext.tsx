import { createContext, useState, useContext, type ReactNode } from "react";

interface AuthContextType {
	isAdmin: boolean;
	setIsAdmin: (isAdmin: boolean) => void;
	user: UserType | null;
	setUser: (user: UserType | null) => void;
}

interface UserType {
	id: number;
	username: String;
	last_login: String;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider = ({ children }: { children: ReactNode }) => {
	const [isAdmin, setIsAdmin] = useState<boolean>(false);
	const [user, setUser] = useState<UserType | null>(null);

	return (
		<AuthContext.Provider value={{ isAdmin, setIsAdmin, user, setUser }}>
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
