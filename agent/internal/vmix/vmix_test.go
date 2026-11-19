package vmix

import "testing"

const sample = `<vmix><version>27.0.0.49</version><edition>4K</edition>
<inputs>
<input key="aaa" number="1" type="Colour" title="Banner Sunday" shortTitle="Banner Sunday" state="Paused" position="0" duration="0" loop="False">Banner Sunday</input>
<input key="bbb" number="2" type="VideoList" title="GM_A - 3-trip.mp4" shortTitle="GM_A" state="Running" position="4100" duration="84000" loop="False" selectedIndex="1">GM_A - 3-trip.mp4</input>
</inputs>
<preview>1</preview><active>1</active>
<mix number="2"><preview>1</preview><active>2</active></mix>
</vmix>`

func TestParse(t *testing.T) {
	st, err := Parse([]byte(sample))
	if err != nil {
		t.Fatal(err)
	}
	if st.Active[1] != 1 || st.Active[2] != 2 {
		t.Fatalf("active: %+v", st.Active)
	}
	in := st.InputByName("GM_A")
	if in == nil || in.Position != 4100 || in.Duration != 84000 || in.State != "Running" {
		t.Fatalf("input: %+v", in)
	}
	if got := st.Mixes(); len(got) != 2 || got[1] != 2 {
		t.Fatalf("mixes: %v", got)
	}
}
