import "../../App.css";
import {
	Controller,
	type Control,
	type FieldPath,
	type FieldValues,
} from "react-hook-form";
import {
	type Accept,
	type DropzoneOptions,
	useDropzone,
} from "react-dropzone";
import {
	type CSSProperties,
	type FC,
	type ChangeEventHandler,
	useMemo,
	useState,
} from "react";

interface Props<TFieldValues extends FieldValues> {
	label: string;
	name: string;
	control: Control<TFieldValues>;
	multiple?: boolean;
	maxFiles?: number;
	accept?: string | string[] | Accept;
	style?: CSSProperties;
	onSubmit?: (files: File[]) => void;
	rest?: Partial<DropzoneOptions>;
	helperText?: string;
}

export default function MyDropzone<TFieldValues extends FieldValues>(
	props: Props<TFieldValues>,
) {
	return (
		<Controller
			name={props.name as FieldPath<TFieldValues>}
			control={props.control}
			render={({ field: { onChange }, fieldState: { error } }) => (
				<Dropzone
					multiple={props.multiple}
					maxFiles={props.maxFiles}
					onChange={(e) =>
						onChange(
							props.multiple ? e.target.files : e.target.files?.[0] ?? null
						)
					}
					onSubmit={props.onSubmit}
					accept={props.accept}
					style={props.style}
					error={Boolean(error)}
					helperText={error ? error.message : props.helperText}
					{...props.rest}
				/>
			)}
		/>
	);
}

const baseStyle: CSSProperties = {
	flex: 1,
	display: "flex",
	flexDirection: "column",
	alignItems: "center",
	padding: "20px",
	borderWidth: 2,
	borderRadius: 2,
	borderColor: "#eeeeee",
	borderStyle: "dashed",
	backgroundColor: "#fafafa",
	color: "#bdbdbd",
	outline: "none",
	transition: "border .24s ease-in-out",
};

const focusedStyle: Partial<CSSProperties> = {
	borderColor: "#2196f3",
};

const acceptStyle: Partial<CSSProperties> = {
	borderColor: "#00e676",
};

const rejectStyle: Partial<CSSProperties> = {
	borderColor: "#ff1744",
};

const Dropzone: FC<{
	multiple?: boolean;
	maxFiles?: number;
	onChange?: ChangeEventHandler<HTMLInputElement>;
	style?: CSSProperties;
	onSubmit?: (files: File[]) => void;
	accept?: string | string[] | Accept;
	error?: boolean;
	helperText?: string;
	rest?: Partial<DropzoneOptions>;
}> = ({
	multiple,
	maxFiles,
	onChange,
	style: customStyle,
	onSubmit,
	accept,
	error,
	helperText,
	rest,
}) => {
	const [uploadedFiles, setUploadedFiles] = useState<File[]>([]);

	const { getRootProps, getInputProps, isFocused, isDragAccept, isDragReject } =
		useDropzone({
			multiple,
			maxFiles,
			accept:
				typeof accept === "object" && !Array.isArray(accept)
					? accept
					: Array.isArray(accept)
						? Object.fromEntries(accept.map((type) => [type, []]))
						: accept
							? { [accept]: [] }
							: undefined,
			onDropAccepted: (acceptedFiles) => {
				setUploadedFiles((prev) => [...prev, ...acceptedFiles]);
				if (onSubmit) {
					onSubmit(acceptedFiles);
				}
			},
			...rest,
		});

	const style = useMemo(
		() => ({
			...baseStyle,
			...customStyle,
			...(isFocused ? focusedStyle : {}),
			...(isDragAccept ? acceptStyle : {}),
			...(isDragReject ? rejectStyle : {}),
		}),
		[customStyle, isFocused, isDragAccept, isDragReject]
	);

	return (
		<div className="container">
			<div {...getRootProps({ style })}>
				<input {...getInputProps({ onChange })} />
				<p>
					{multiple
						? "Wybierz pliki lub przeciągnij je tutaj"
						: "Wybierz plik lub przeciągnij go tutaj"}
				</p>
			</div>
			<div>
				{uploadedFiles.length > 0 && (
					<ul>
						{uploadedFiles.map((file, index) => (
							<li key={index}>{file.name}</li>
						))}
					</ul>
				)}
			</div>
			<div>
				{(helperText || error) && (
					<p
						style={{ color: error ? "#d32f2f" : "#000000de", fontSize: "12px" }}
					>
						{helperText || "Wystąpił błąd podczas przesyłania plików."}
					</p>
				)}
			</div>
		</div>
	);
};
