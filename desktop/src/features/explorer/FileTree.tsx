import { memo, useCallback } from "react";
import { FixedSizeList as List } from "react-window";
import { ChevronRight, ChevronDown, File, Folder, FolderOpen } from "lucide-react";
import { cn } from "@/lib/utils";
import { useProjectStore, type TreeNode } from "@/store/projectStore";
import { useEditorStore } from "@/store/editorStore";
import { useUIStore } from "@/store/uiStore";
import { bridge } from "@/lib/krythBridge";

const ROW_HEIGHT = 26;

export const FileTree = memo(function FileTree({ height }: { height: number }) {
  const { flatNodes, toggleExpand } = useProjectStore();
  const openTab = useEditorStore((s) => s.openTab);
  const setWorkspaceTab = useUIStore((s) => s.setWorkspaceTab);

  const handleClick = useCallback(
    async (node: TreeNode) => {
      if (node.is_dir) {
        if (!node.loaded) {
          try {
            const children = await bridge.listFiles(node.path);
            toggleExpand(node.path, children);
          } catch {
            toggleExpand(node.path);
          }
        } else {
          toggleExpand(node.path);
        }
      } else {
        try {
          const content = await bridge.readFile(node.path);
          openTab({ path: node.path, filename: node.name, content, language: "" });
        } catch {
          openTab({ path: node.path, filename: node.name, content: "", language: "" });
        }
        setWorkspaceTab("editor");
      }
    },
    [toggleExpand, openTab, setWorkspaceTab]
  );

  return (
    <List
      height={height}
      width="100%"
      itemCount={flatNodes.length}
      itemSize={ROW_HEIGHT}
    >
      {({ index, style }) => (
        <FileRow
          key={flatNodes[index].path}
          node={flatNodes[index]}
          style={style}
          onClick={handleClick}
        />
      )}
    </List>
  );
});

const FileRow = memo(function FileRow({
  node,
  style,
  onClick,
}: {
  node: TreeNode;
  style: React.CSSProperties;
  onClick: (node: TreeNode) => void;
}) {
  return (
    <div
      style={style}
      onClick={() => onClick(node)}
      className="flex items-center gap-1 px-2 cursor-pointer hover:bg-surface2 transition-colors duration-120 group"
    >
      {/* Indent */}
      <span style={{ width: node.depth * 12 }} className="shrink-0" />

      {/* Expand arrow for dirs */}
      {node.is_dir ? (
        <span className="w-4 shrink-0 text-muted">
          {node.expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        </span>
      ) : (
        <span className="w-4 shrink-0" />
      )}

      {/* Icon */}
      <span className="shrink-0 text-muted">
        {node.is_dir ? (
          node.expanded ? <FolderOpen size={13} className="text-accent/70" /> : <Folder size={13} className="text-accent/70" />
        ) : (
          <File size={12} />
        )}
      </span>

      {/* Name */}
      <span
        className={cn(
          "text-xs truncate flex-1",
          node.is_dir ? "text-text font-medium" : "text-muted group-hover:text-text"
        )}
      >
        {node.name}
      </span>
    </div>
  );
});
