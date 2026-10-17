--  Buffered output of JSON and request text. Everything written is either
--  ASCII from this program or a decoded, printable character, so the only
--  escapes JSON needs are for '"' and '\'.

with Ofx.IO;
with Ofx.Text;

package Ofx.Json with SPARK_Mode is

   Capacity : constant := 65_536;

   type Writer is record
      Target : Ofx.IO.Stream := Ofx.IO.Standard_Output;
      Data   : String (1 .. Capacity) := [others => ' '];
      Last   : Natural range 0 .. Capacity := 0;
      Failed : Boolean := False;   --  a write to the stream failed
   end record;

   procedure Flush (W : in out Writer)
   with Global => (In_Out => Ofx.IO.Output_State), Post => W.Last = 0;

   procedure Put (W : in out Writer; C : Character)
   with Global => (In_Out => Ofx.IO.Output_State);

   procedure Put (W : in out Writer; S : String)
   with Global => (In_Out => Ofx.IO.Output_State);

   --  N in decimal, at least Width digits.
   procedure Put_Number (W : in out Writer; N : Natural; Width : Positive := 1)
   with Global => (In_Out => Ofx.IO.Output_State), Pre => Width <= 10;

   --  C as UTF-8; with Escaped, '"' and '\' get a JSON backslash.
   procedure Put_Character (W : in out Writer; C : Ofx.Text.Code_Point; Escaped : Boolean)
   with Global => (In_Out => Ofx.IO.Output_State), Pre => not Ofx.Text.Is_Surrogate (C);

   --  Source (S) decoded, as a quoted JSON string.
   procedure Put_String
     (W          : in out Writer;
      Source     : String;
      S          : Span;
      Encoding   : Charset;
      References : Boolean)
   with
     Global => (In_Out => Ofx.IO.Output_State),
     Pre    => Source'First = 1 and then Source'Last <= Max_Input and then Within (S, Source);

end Ofx.Json;
