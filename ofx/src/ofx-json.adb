package body Ofx.Json with SPARK_Mode is

   use Ofx.Text;

   procedure Flush (W : in out Writer) is
      Ok : Boolean;
   begin
      if W.Last > 0 and then not W.Failed then
         Ofx.IO.Write (W.Target, W.Data (1 .. W.Last), Ok);
         if not Ok then
            W.Failed := True;
         end if;
      end if;
      W.Last := 0;
   end Flush;

   procedure Put (W : in out Writer; C : Character) is
   begin
      if W.Last = Capacity then
         Flush (W);
      end if;
      W.Last := W.Last + 1;
      W.Data (W.Last) := C;
   end Put;

   procedure Put (W : in out Writer; S : String) is
   begin
      for C of S loop
         Put (W, C);
      end loop;
   end Put;

   procedure Put_Number (W : in out Writer; N : Natural; Width : Positive := 1) is
      Figures : String (1 .. 10);
      Value  : Natural := N;
      First  : Positive := Figures'Last;
   begin
      --  Natural'Last has ten digits.
      for Index in reverse Figures'Range loop
         Figures (Index) := Character'Val (Character'Pos ('0') + Value mod 10);
         Value := Value / 10;
      end loop;
      for Index in Figures'Range loop
         if Figures (Index) /= '0' then
            First := Index;
            exit;
         end if;
      end loop;
      First := Positive'Min (First, Figures'Last - Width + 1);
      Put (W, Figures (First .. Figures'Last));
   end Put_Number;

   procedure Put_Character (W : in out Writer; C : Code_Point; Escaped : Boolean) is
      function Byte (Value : Natural) return Character is (Character'Val (Value))
      with Pre => Value <= 255;
   begin
      if C < 16#80# then
         if Escaped and then (C = 34 or else C = 92) then
            Put (W, '\');
         end if;
         Put (W, Byte (C));
      elsif C < 16#800# then
         Put (W, Byte (16#C0# + C / 64));
         Put (W, Byte (16#80# + C mod 64));
      elsif C < 16#10000# then
         Put (W, Byte (16#E0# + C / 4096));
         Put (W, Byte (16#80# + (C / 64) mod 64));
         Put (W, Byte (16#80# + C mod 64));
      else
         Put (W, Byte (16#F0# + C / 262_144));
         Put (W, Byte (16#80# + (C / 4096) mod 64));
         Put (W, Byte (16#80# + (C / 64) mod 64));
         Put (W, Byte (16#80# + C mod 64));
      end if;
   end Put_Character;

   procedure Put_String
     (W          : in out Writer;
      Source     : String;
      S          : Span;
      Encoding   : Charset;
      References : Boolean)
   is
      Position : Positive := S.First;
      Next     : Positive;
      Char     : Code_Point;
      Valid    : Boolean;
   begin
      Put (W, '"');
      if not Is_Empty (S) then
         while Position <= S.Last loop
            pragma Loop_Invariant (Position in S.First .. S.Last);
            pragma Loop_Variant (Increases => Position);
            Decode (Source, Position, S.Last, Encoding, References, Char, Next, Valid);
            --  Values are validated when stored; U+FFFD only guards the
            --  impossible case.
            Put_Character (W, (if Valid then Char else 16#FFFD#), Escaped => True);
            Position := Next;
         end loop;
      end if;
      Put (W, '"');
   end Put_String;

end Ofx.Json;
