import { useCallback } from "react";
import QueryOwnerEditorDialog from "@/components/QueryOwnerEditorDialog";
import useUpdateQuery from "./useUpdateQuery";
import recordEvent from "@/services/recordEvent";

export default function useQueryOwnerEditorDialog(query, onChange) {

    const updateQuery = useUpdateQuery(query, onChange);

    return useCallback(() => { 
      QueryOwnerEditorDialog.showModal({
        context: "query",
        author: query.user,
      }).onClose(user => {
        recordEvent("edit_query_owner", "query", query.id);
        updateQuery({ user: user });
      });
    }, [query.id, query.user, updateQuery]);
}