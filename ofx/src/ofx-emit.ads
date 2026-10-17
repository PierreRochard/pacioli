--  Writes a parsed document as JSON ("pacioli-ofx/1", described in
--  ../README.md), or a parse error. Absent and empty elements are null.

with Ofx.IO;
with Ofx.Json;
with Ofx.Model;
with Ofx.Parser;

package Ofx.Emit with SPARK_Mode is

   procedure Document (W : in out Json.Writer; Text : String; Doc : Model.Document)
   with
     Global => (In_Out => Ofx.IO.Output_State),
     Pre    => Text'First = 1 and then Text'Last <= Max_Input;

   procedure Error (W : in out Json.Writer; Text : String; E : Parser.Error_Record)
   with
     Global => (In_Out => Ofx.IO.Output_State),
     Pre    => Parser.Well_Formed (Text, E);

end Ofx.Emit;
