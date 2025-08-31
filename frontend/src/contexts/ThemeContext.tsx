import { createContext, useState, useMemo, useContext, type ReactNode } from "react";
import {
	type Color,
	createTheme,
	ThemeProvider as MuiThemeProvider,
	type PaletteMode,
	type PaletteColorOptions,
} from "@mui/material";
import {
	blueGrey,
	teal,
	deepPurple,
	indigo,
	green,
	deepOrange,
} from "@mui/material/colors";

// Map of color names to color objects
export const colorMap: { [key: string]: Color | PaletteColorOptions } = {
	blueGrey,
	teal,
	deepPurple,
	indigo,
	green,
	deepOrange,
};

interface ThemeContextType {
	toggleMode: () => void;
	setColor: (colorName: string) => void;
	mode: PaletteMode;
	primaryColor: Color | PaletteColorOptions;
}

const ThemeContext = createContext<ThemeContextType | undefined>(undefined);

export const CustomThemeProvider = ({ children }: { children: ReactNode }) => {
	const [mode, setMode] = useState<PaletteMode>(() => {
		const savedMode = localStorage.getItem("themeMode") as PaletteMode | null;
		return savedMode || "light";
	});
	const [primaryColor, setPrimaryColor] = useState<Color | PaletteColorOptions>(
		() => {
			const savedColorName = localStorage.getItem("primaryColorName");
			// Use the map to find the color object by its saved name
			return savedColorName && colorMap[savedColorName]
				? colorMap[savedColorName]
				: blueGrey;
		}
	);

	const theme = useMemo(
		() =>
			createTheme({
				palette: {
					mode,
					primary: primaryColor as PaletteColorOptions,
				},
			}),
		[mode, primaryColor]
	);

	const toggleMode = () => {
		const newMode = mode === "light" ? "dark" : "light";
		setMode(newMode);
		localStorage.setItem("themeMode", newMode);
	};

	const setColor = (colorName: string) => {
		const newColor = colorMap[colorName];
		if (newColor) {
			setPrimaryColor(newColor);
			// Save the color's name, not the object
			localStorage.setItem("primaryColorName", colorName);
		}
	};

	return (
		<ThemeContext.Provider value={{ toggleMode, setColor, mode, primaryColor }}>
			<MuiThemeProvider theme={theme}>{children}</MuiThemeProvider>
		</ThemeContext.Provider>
	);
};

export const useCustomTheme = () => {
	const context = useContext(ThemeContext);
	if (!context) {
		throw new Error("useCustomTheme must be used within a CustomThemeProvider");
	}
	return context;
};
