--  Reads one OFX response into a Model.Document. The grammar is the subset of
--  OFX that Pacioli imports: the sign-on response and bank and credit-card
--  statement responses. Anything else is rejected, except private extensions
--  (names with a '.') and the informational aggregates listed in the body.
--  Values are checked against Ofx.Values; which elements are required is
--  Pacioli's policy, decided after parsing.

with Ofx.Model; use Ofx.Model;

package Ofx.Parser with SPARK_Mode is

   Max_Depth : constant := 32;

   type Error_Kind is
     (No_Error,
      Header_Invalid,
      Syntax_Invalid,        --  malformed markup, mismatched end tag, stray text
      Element_Unsupported,
      Element_Duplicate,
      Value_Invalid,
      Limit_Exceeded);       --  nesting, statements, or transactions

   type Path_Array is array (1 .. Max_Depth + 1) of Span;

   --  Where the parse stopped: the element names from <OFX> down to the one
   --  at fault, and the input position. Never a value.
   type Error_Record is record
      Kind     : Error_Kind := No_Error;
      Position : Natural := 0;
      Depth    : Natural range 0 .. Max_Depth + 1 := 0;
      Path     : Path_Array := [others => No_Span];
   end record;

   function Well_Formed (Text : String; E : Error_Record) return Boolean is
     (for all I in 1 .. E.Depth => Within (E.Path (I), Text));

   procedure Parse (Text : String; Doc : in out Document; Error : out Error_Record)
   with
     Pre  => Text'First = 1 and then Text'Last <= Max_Input,
     Post => Well_Formed (Text, Error);

end Ofx.Parser;
