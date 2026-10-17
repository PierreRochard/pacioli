--  Decoding of character data: character references, then the document's
--  charset. Every decoded character is a Unicode scalar value that is not a
--  control character, so it can be written as UTF-8 JSON without escapes
--  other than '"' and '\'.

package Ofx.Text with SPARK_Mode is

   subtype Code_Point is Natural range 0 .. 16#10FFFF#;

   function Is_Surrogate (C : Code_Point) return Boolean is
     (C in 16#D800# .. 16#DFFF#);

   function Is_Control (C : Code_Point) return Boolean is
     (C < 16#20# or else C in 16#7F# .. 16#9F#);

   function Is_Printable (C : Code_Point) return Boolean is
     (not Is_Surrogate (C) and then not Is_Control (C));

   --  Decode the character at Text (Position) within Text (Position .. Last).
   --  With References, '&amp;' '&lt;' '&gt;' '&quot;' '&apos;' and numeric
   --  references are decoded; any other '&' is a literal ampersand. Next is
   --  the position after the character, valid or not.
   procedure Decode
     (Text       : String;
      Position   : Positive;
      Last       : Natural;
      Encoding   : Charset;
      References : Boolean;
      Char       : out Code_Point;
      Next       : out Positive;
      Valid      : out Boolean)
   with
     Pre  => Text'First = 1 and then Text'Last <= Max_Input
             and then Position <= Last and then Last <= Text'Last,
     Post => Next in Position + 1 .. Last + 1
             and then (if Valid then Is_Printable (Char));

   --  Check that S decodes completely; Count is its length in characters.
   procedure Measure
     (Text       : String;
      S          : Span;
      Encoding   : Charset;
      References : Boolean;
      Count      : out Natural;
      Valid      : out Boolean)
   with
     Pre  => Text'First = 1 and then Text'Last <= Max_Input and then Within (S, Text),
     Post => Count <= Length (S);

end Ofx.Text;
