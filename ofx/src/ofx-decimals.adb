with Ofx.Chars; use Ofx.Chars;

package body Ofx.Decimals with SPARK_Mode is

   procedure Parse (Text : String; S : Span; Result : out Decimal; Ok : out Boolean) is
      Position : Positive;
      Negative : Boolean := False;
      Whole    : Span;
      Fraction : Span := No_Span;
      Nonzero  : Boolean;
   begin
      Result := (others => <>);
      Ok := False;
      if Is_Empty (S) then
         return;
      end if;

      Position := S.First;
      if Text (Position) in '+' | '-' then
         Negative := Text (Position) = '-';
         Position := Position + 1;
      end if;

      Whole := (First => Position, Last => Position - 1);
      while Position <= S.Last and then Is_Digit (Text (Position)) loop
         pragma Loop_Invariant (Position in S.First .. S.Last);
         pragma Loop_Invariant (Whole.Last = Position - 1 and then Whole.First >= S.First);
         pragma Loop_Invariant (for all I in Whole.First .. Position - 1 => Is_Digit (Text (I)));
         pragma Loop_Variant (Increases => Position);
         Whole.Last := Position;
         Position := Position + 1;
      end loop;

      if Position <= S.Last and then Text (Position) in '.' | ',' then
         Position := Position + 1;
         Fraction := (First => Position, Last => Position - 1);
         while Position <= S.Last and then Is_Digit (Text (Position)) loop
            pragma Loop_Invariant (Position in S.First .. S.Last);
            pragma Loop_Invariant (Fraction.Last = Position - 1
                                   and then Fraction.First >= S.First);
            pragma Loop_Invariant
              (for all I in Fraction.First .. Position - 1 => Is_Digit (Text (I)));
            pragma Loop_Variant (Increases => Position);
            Fraction.Last := Position;
            Position := Position + 1;
         end loop;
      end if;

      if Position <= S.Last or else (Is_Empty (Whole) and then Is_Empty (Fraction)) then
         return;
      end if;

      --  Leading zeros carry no value; Pacioli receives "0.50", not "00.50".
      while not Is_Empty (Whole) and then Text (Whole.First) = '0' loop
         pragma Loop_Invariant (Within (Whole, Text));
         pragma Loop_Invariant (for all I in Whole.First .. Whole.Last => Is_Digit (Text (I)));
         pragma Loop_Variant (Increases => Whole.First);
         Whole.First := Whole.First + 1;
      end loop;
      if Is_Empty (Whole) then
         Whole := No_Span;
      end if;
      if Is_Empty (Fraction) then
         Fraction := No_Span;
      end if;

      if Length (Whole) > Max_Integer_Digits or else Length (Fraction) > Max_Fraction_Digits then
         return;
      end if;

      Nonzero := not Is_Empty (Whole);
      for I in Fraction.First .. Fraction.Last loop
         if Text (I) /= '0' then
            Nonzero := True;
         end if;
      end loop;

      Result := (Negative => Negative and then Nonzero, Whole => Whole, Fraction => Fraction);
      Ok := True;
   end Parse;

end Ofx.Decimals;
